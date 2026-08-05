"""REFERENCES / INIT_BY 엣지 — 결정론 식별자 매칭 (같은 모듈 + 모듈 너머).

antlr JSON 에 "변수 참조" 노드가 없으므로, 본문 텍스트의 식별자 토큰을
Variable/Constant 이름과 매칭한다. LLM 0회 — 순수 결정론.

- REFERENCES: 본문이 참조하는 변수/상수 → CodeNode → 그 노드.
- INIT_BY:    변수/상수 정의 시 참조하는 다른 변수/상수 → (Variable|Constant) → 출처.
             "정의 시 참조" 의미: ``=`` 우변뿐 아니라 변수 size 표현식
             (``[LEN +1]``) · 상수 식 (``#define B (A+1)``) 모두 포함.
             antlr (C parser) 가 size 표현식까지 ``initValue`` 에 통합해 출력하므로
             reader 는 ``Variable.init_value`` / ``Constant.value`` 만 보면 끝.

2단 패스 (계획 §2.8):
- **Pass 1 — 같은 파일(모듈) 안.** 전 언어 공통.
- **Pass 2 — 모듈 너머.** "코드에 명시된 소유권 신호(import)" 만 따라간다 — 추측 안 함.
  구문·식별자·상속은 ontology profile, 실행은 범용 namespace/source-module/local-file
  resolver가 소유한다. import 단서를 emit하면서 profile이 없으면 명시 실패한다.

오탐 차단: 주석·문자열 리터럴 제거 후 식별자 토큰 단위 정확 일치. import 로 범위를
좁히므로 동명이인(타 모듈 같은 이름)을 오연결하지 않는다.
상속 조상 매칭 (spec 111): Java ``extends``/``implements``·Python ``class B(A)`` 선언을
ontology의 import profile로 선언을 파싱해 조상 모듈의 필드/상수까지 맨이름 매칭 (자기 모듈
우선 섀도잉, 깊이 상한, 단독 후보만 — 과대근사 금지).
※ 못 잡는 것(설계상 범위, silent fail 아님): Python 인스턴스 속성 접근
  (``self._cfg.x`` — 선언 없는 속성은 Variable 노드 자체가 없음, 타입 추론 필요).
"""
from __future__ import annotations

import re
from collections import defaultdict
from functools import lru_cache
from pathlib import Path
from typing import Awaitable, Callable

from graph.schema.graph_edges import InitBy, References
from graph.schema.graph_nodes import Constant, CodeNode, Module, Variable
from pipeline.languages import (
    lang_of,
    reference_import_profile,
    reference_inheritance_profile,
    source_lexicon,
)
from pipeline.quality_gates.lexing import split_source, without_string_literals
from shared.files.cache import SourceFileCache

_INHERIT_DEPTH = 3                          # 조상 탐색 상한 — 순환·폭주 차단

# module_id → {식별자 이름: [(node_id, 라벨)]}
_Targets = dict[str, dict[str, list[tuple[str, str]]]]

# Pass 2 미지원 언어 — 언어당 1회만 log (노이즈 차단).

from pipeline.steps.step2_load_ast.import_scopes import (
    _Scope, _Targets, _build_scopes, _file_indexes, _targets_by_module,
)


@lru_cache(maxsize=None)
def _identifier_regex(lang: str) -> re.Pattern:
    pattern = source_lexicon(lang).get("identifier_pattern")
    if not isinstance(pattern, str) or not pattern.strip():
        raise ValueError(f"source_lexicon.{lang}.identifier_pattern 가 필요하다")
    return re.compile(pattern)


@lru_cache(maxsize=None)
def _qualified_regex(lang: str) -> re.Pattern:
    profile = reference_import_profile(lang)
    separator = str(profile.get("separator") or "")
    if not separator:
        # direct-name resolver는 qualified lookup을 사용하지 않는다.
        return re.compile(r"(?!)")
    identifier = source_lexicon(lang)["identifier_pattern"]
    return re.compile(
        rf"({identifier})\s*{re.escape(separator)}\s*({identifier})"
    )

async def link_references(
    code_nodes: list[CodeNode],
    variables: list[Variable],
    constants: list[Constant],
    import_refs: list[dict],
    source_cache: SourceFileCache | None = None,
    checkpoint: Callable[[], Awaitable[None]] | None = None,
    modules: list[Module] = (),
) -> tuple[list[References], list[InitBy]]:
    """본문 → REFERENCES, 변수/상수 ``initValue`` → INIT_BY (같은 모듈 + import 한 모듈).

    INIT_BY 는 변수+상수 모두 출발 가능 — 정의 시 외부 토큰 참조하면 잡힌다.
    ``modules`` 가 주어지면 상속 조상 모듈의 필드/상수까지 맨이름 매칭한다 (spec 111 —
    Java ``extends``/``implements``·Python ``class B(A)``, 자기 모듈 우선 섀도잉).

    ``source_cache`` 는 호출부(step2)가 주입 — 같은 step 안에서 캐시를 **두 벌** 만들면
    파일을 두 번 읽는다 (spec 046 FR-008: 종전엔 여기와 load_ast_step 이 각자 만들었다).
    """
    targets = _targets_by_module(variables, constants)
    modules_by_file, basename_index = _file_indexes(variables, constants)
    scopes = _build_scopes(import_refs, targets, modules_by_file, basename_index)
    cache = source_cache or SourceFileCache()
    ancestors = _ancestor_map(list(modules), scopes, cache)
    if checkpoint is not None:
        await checkpoint()
    return (
        await _link_code_bodies(
            code_nodes, targets, scopes, cache, checkpoint, ancestors,
        ),
        await _link_init_values(variables, constants, targets, scopes, checkpoint, ancestors),
    )


# ── 상속 조상 맵 (spec 111) ─────────────────────────────────


def _ancestor_map(
    modules: list[Module], scopes: dict[str, "_Scope"], cache: SourceFileCache,
) -> dict[str, list[str]]:
    """module_id → 조상 module_id 목록 (가까운 순, 깊이 상한).

    부모 해소 순서 = 같은 파일의 동명 모듈 → import 소유자 맵의 **단독** 후보.
    복수 후보·미발견은 포기 (과대근사 금지). C 등 명단 밖 언어는 자연히 빈 맵.
    """
    by_name_in_file: dict[tuple[str, str], list[str]] = defaultdict(list)
    for m in modules:
        by_name_in_file[(m.file_path, m.name)].append(m.id)

    parents: dict[str, list[str]] = {}
    for m in modules:
        lang = lang_of(Path(m.file_path).suffix)
        profile = reference_inheritance_profile(lang)
        if profile is None:
            continue
        decl_re = re.compile(str(profile["pattern"]))
        # 원문 주입(step2 후반)보다 먼저 호출되므로 캐시에서 선언 라인을 직접 읽는다.
        lines = cache.lines(m.file_path)
        idx = max(m.start_line, 1) - 1
        decl_line = lines[idx].strip() if idx < len(lines) else ""
        match = decl_re.search(decl_line)
        if match is None:
            continue
        resolved: list[str] = []
        parents_text = match.groupdict().get("parents")
        if parents_text is None:
            raise ValueError(
                f"reference_imports.{lang}.inheritance_pattern 에 parents 그룹이 없다"
            )
        parent_separator = str(profile.get("parent_separator") or ",")
        owner_separator = str(profile.get("separator") or ".")
        generic_open = str(profile.get("generic_open") or "")
        ignored = {str(name) for name in (profile.get("ignored_parents") or ())}
        for raw_name in parents_text.split(parent_separator):
            name = raw_name.strip().rsplit(owner_separator, 1)[-1]
            if generic_open:
                name = name.split(generic_open, 1)[0].strip()
            if not name or name in ignored:
                continue
            same_file = by_name_in_file.get((m.file_path, name), [])
            if len(same_file) == 1:
                resolved.append(same_file[0])
                continue
            scope = scopes.get(m.file_path)
            owner_hits = list(getattr(scope, "owner_map", {}).get(name, ())) if scope else []
            if len(owner_hits) == 1:                       # 단독 후보만 — 중의는 포기
                resolved.append(owner_hits[0])
        if resolved:
            parents[m.id] = resolved

    ancestors: dict[str, list[str]] = {}
    for mid in parents:
        chain: list[str] = []
        frontier = list(parents.get(mid, []))
        depth = 0
        while frontier and depth < _INHERIT_DEPTH:
            next_frontier: list[str] = []
            for pid in frontier:
                if pid == mid or pid in chain:
                    continue                                # 순환 차단
                chain.append(pid)
                next_frontier.extend(parents.get(pid, []))
            frontier = next_frontier
            depth += 1
        if chain:
            ancestors[mid] = chain
    return ancestors


# ── 인덱스 빌드 ──────────────────────────────────────────────

async def _link_code_bodies(
    code_nodes: list[CodeNode], targets: _Targets,
    scopes: dict[str, _Scope], cache: SourceFileCache,
    checkpoint: Callable[[], Awaitable[None]] | None,
    ancestors: dict[str, list[str]] | None = None,
) -> list[References]:
    """본문 라인 스캔 — 같은 모듈(Pass 1) + 상속 조상(spec 111) + import 모듈(Pass 2).

    코드노드당 타겟별 1개 (첫 등장 라인을 evidence 로). 섀도잉: 같은 이름이 자기
    모듈에 있으면 조상 매칭은 건너뜀 (chain 이 own 먼저 시도).
    """
    out: list[References] = []
    for fn in code_nodes:
        if checkpoint is not None:
            await checkpoint()
        own = targets.get(fn.owner_id) or {}
        chain = _lookup_chain(fn.owner_id, own, targets, ancestors)
        scope = scopes.get(fn.file_path)
        if not chain and scope is None:
            continue
        lines = cache.lines(fn.file_path)
        start = max(fn.start_line - 1, 0)
        lang = lang_of(Path(fn.file_path).suffix)
        active_text = split_source(
            "\n".join(lines[start:fn.end_line]), lang,
        ).live()
        active_lines = without_string_literals(active_text, lang).split("\n")
        identifier_re = _identifier_regex(lang)
        seen: set[str] = set()
        for offset, text in enumerate(active_lines):
            evidence = start + offset + 1
            for tid, _ in _chain_hits(text, chain, seen, identifier_re):
                out.append(References(fn.id, tid, evidence))
            if scope is not None:
                for tid, _ in _pass2_hits(text, scope, targets, seen):
                    out.append(References(fn.id, tid, evidence))
    return out


def _lookup_chain(
    owner_id: str, own: dict, targets: _Targets,
    ancestors: dict[str, list[str]] | None,
) -> list[dict]:
    """이름 탐색 사슬 — 자기 모듈 먼저(섀도잉), 그다음 상속 조상 순서대로."""
    chain = [own] if own else []
    for pid in (ancestors or {}).get(owner_id, ()):
        parent_targets = targets.get(pid)
        if parent_targets:
            chain.append(parent_targets)
    return chain


def _chain_hits(
    text: str, chain: list[dict], seen: set[str], identifier_re: re.Pattern,
) -> list[tuple[str, str]]:
    """토큰마다 사슬의 **첫 번째로 이름을 가진 dict 만** 매칭 — 자기 모듈 우선 섀도잉."""
    hits: list[tuple[str, str]] = []
    for token in sorted(set(identifier_re.findall(text))):
        for names in chain:
            entries = names.get(token)
            if entries:
                hits += _collect(entries, seen)
                break
    return hits
async def _link_init_values(
    variables: list[Variable], constants: list[Constant],
    targets: _Targets, scopes: dict[str, _Scope],
    checkpoint: Callable[[], Awaitable[None]] | None,
    ancestors: dict[str, list[str]] | None = None,
) -> list[InitBy]:
    """변수/상수 ``initValue`` 의 식별자 → 출처 노드 INIT_BY (같은 모듈 + 상속 + import).

    Variable.init_value / Constant.value 둘 다 antlr 가 통합 출력 — size 표현식
    (``[LEN +1]``) · ``=`` 우변 · 상수 식 모두 포함. reader 는 그 텍스트만 본다.
    """
    out: list[InitBy] = []
    for node, from_id, init_text in _init_iter(variables, constants):
        if checkpoint is not None:
            await checkpoint()
        if not init_text:
            continue
        lang = lang_of(Path(node.file_path).suffix)
        text = without_string_literals(split_source(init_text, lang).live(), lang)
        identifier_re = _identifier_regex(lang)
        own = targets.get(node.owner_id) or {}
        chain = _lookup_chain(node.owner_id, own, targets, ancestors)
        scope = scopes.get(node.file_path)
        seen: set[str] = {from_id}                       # 자기참조 제외
        hits = _chain_hits(text, chain, seen, identifier_re)
        if scope is not None:
            hits += _pass2_hits(text, scope, targets, seen)
        for source_id, _ in hits:
            out.append(InitBy(from_id, source_id))
    return out


def _init_iter(variables: list[Variable], constants: list[Constant]):
    """(node, from_id, init_text) 3-튜플 순회 — 변수+상수 통합 처리용.

    필드명이 모델별로 다르므로 (Variable.init_value vs Constant.value) 분기 한 곳에
    가둔다 — _link_init_values 본문은 단일 흐름 유지.
    """
    for v in variables:
        yield v, v.id, v.init_value
    for c in constants:
        yield c, c.id, c.value


def _bare_hits(
    text: str, names: dict[str, list[tuple[str, str]]], seen: set[str],
    identifier_re: re.Pattern,
) -> list[tuple[str, str]]:
    """텍스트의 식별자 토큰을 ``names`` 테이블과 정확 일치 — 미방문분만."""
    hits: list[tuple[str, str]] = []
    for token in sorted(set(identifier_re.findall(text))):
        hits += _collect(names.get(token, ()), seen)
    return hits


def _pass2_hits(
    text: str, scope: _Scope, targets: _Targets, seen: set[str],
) -> list[tuple[str, str]]:
    """Match normalized direct, qualified-owner, and direct-member ownership."""
    identifier_re = _identifier_regex(scope.lang)
    hits = _bare_hits(text, scope.direct_names, seen, identifier_re)
    for owner, member in _qualified_regex(scope.lang).findall(text):
        for mid in scope.owner_map.get(owner, ()):
            hits += _collect(targets.get(mid, {}).get(member, ()), seen)
    if scope.bare_map:
        for token in sorted(set(identifier_re.findall(text))):
            for mid in scope.bare_map.get(token, ()):
                hits += _collect(targets.get(mid, {}).get(token, ()), seen)
    return hits


def _collect(
    entries: list[tuple[str, str]] | tuple, seen: set[str],
) -> list[tuple[str, str]]:
    """(node_id, label) 목록에서 아직 안 본 것만 — ``seen`` 갱신."""
    hits: list[tuple[str, str]] = []
    for node_id, label in entries:
        if node_id not in seen:
            seen.add(node_id)
            hits.append((node_id, label))
    return hits
