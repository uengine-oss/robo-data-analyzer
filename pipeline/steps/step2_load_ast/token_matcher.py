"""REFERENCES / INIT_BY 엣지 — 결정론 식별자 매칭 (같은 모듈 + 모듈 너머).

antlr JSON 에 "변수 참조" 노드가 없으므로, 본문 텍스트의 식별자 토큰을
Variable/Constant 이름과 매칭한다. LLM 0회 — 순수 결정론.

- REFERENCES: 본문이 참조하는 변수/상수 → CodeNode → 그 노드.
- INIT_BY:    변수/상수 정의 시 참조하는 다른 변수/상수 → (Variable|Constant) → 출처.
             "정의 시 참조" 의미: ``=`` 우변뿐 아니라 변수 size 표현식
             (``[LEN +1]``) · 상수 식 (``#define B (A+1)``) 모두 포함.
             antlr (C parser) 가 size 표현식까지 ``initValue`` 에 통합해 출력하므로
             reader 는 ``Variable.init_value`` / ``Constant.value`` 만 보면 끝.

같은 파일과 Parser-owned 상속만 연결한다. 모듈 너머 참조는 import 문장만으로 대상을
추측하지 않는다. build/module/import environment가 증명한 binding이 들어오기 전까지
canonical import occurrence ledger에 unresolved로 남는다.

오탐 차단: 주석·문자열 리터럴 제거 후 식별자 토큰 단위 정확 일치. import 로 범위를
좁히므로 동명이인(타 모듈 같은 이름)을 오연결하지 않는다.
상속 조상 매칭 (spec 111): Java ``extends``/``implements``·Python ``class B(A)``를
Parser listener AST 필드에서 읽어 조상 모듈의 필드/상수까지 맨이름 매칭 (자기 모듈
우선 섀도잉, finite graph cycle-safe 전수 closure, 단독 후보만 — 과대근사 금지).
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
    reference_inheritance_profile,
    source_lexicon,
)
from pipeline.quality_gates.lexing import split_source, without_string_literals
from shared.files.cache import SourceFileCache

# module_id → {식별자 이름: [(node_id, 라벨)]}
_Targets = dict[str, dict[str, list[tuple[str, str]]]]

# Pass 2 미지원 언어 — 언어당 1회만 log (노이즈 차단).

from pipeline.steps.step2_load_ast.import_scopes import (
    build_import_scope_plan,
)


def _targets_by_module(
    variables: list[Variable], constants: list[Constant],
) -> _Targets:
    """Module ID → exact same-file declared symbol names and graph identities."""
    by_module: _Targets = defaultdict(lambda: defaultdict(list))
    for variable in variables:
        if variable.name:
            by_module[variable.owner_id][variable.name].append(
                (variable.id, "VARIABLE")
            )
    for constant in constants:
        if constant.name:
            by_module[constant.owner_id][constant.name].append(
                (constant.id, "CONSTANT")
            )
    return by_module


@lru_cache(maxsize=None)
def _identifier_regex(lang: str) -> re.Pattern:
    pattern = source_lexicon(lang).get("identifier_pattern")
    if not isinstance(pattern, str) or not pattern.strip():
        raise ValueError(f"source_lexicon.{lang}.identifier_pattern 가 필요하다")
    return re.compile(pattern)


async def link_references(
    code_nodes: list[CodeNode],
    variables: list[Variable],
    constants: list[Constant],
    import_refs: list[dict],
    source_cache: SourceFileCache | None = None,
    checkpoint: Callable[[], Awaitable[None]] | None = None,
    modules: list[Module] = (),
) -> tuple[list[References], list[InitBy]]:
    """본문 → REFERENCES, 변수/상수 ``initValue`` → INIT_BY.

    INIT_BY 는 변수+상수 모두 출발 가능 — 정의 시 외부 토큰 참조하면 잡힌다.
    ``modules`` 가 주어지면 같은 파일에서 exact하게 소유된 상속 조상의 필드/상수까지
    맨이름 매칭한다. 모듈 너머 부모는 import 이름으로 추측하지 않는다.

    ``source_cache`` 는 호출부(step2)가 주입 — 같은 step 안에서 캐시를 **두 벌** 만들면
    파일을 두 번 읽는다 (spec 046 FR-008: 종전엔 여기와 load_ast_step 이 각자 만들었다).
    """
    targets = _targets_by_module(variables, constants)
    import_plan = build_import_scope_plan(import_refs)
    _bind_import_resolution_entries(import_refs, import_plan.ledger)
    cache = source_cache or SourceFileCache()
    ancestors = _ancestor_map(list(modules))
    if checkpoint is not None:
        await checkpoint()
    return (
        await _link_code_bodies(
            code_nodes, targets, cache, checkpoint, ancestors,
        ),
        await _link_init_values(variables, constants, targets, checkpoint, ancestors),
    )


def _bind_import_resolution_entries(
    import_refs: list[dict], ledger: list[dict],
) -> None:
    """Attach the canonical entry ledger without flattening statement evidence."""
    by_fact: dict[str, list[dict]] = defaultdict(list)
    for row in ledger:
        by_fact[row["import_id"]].append(row)
    for ref in import_refs:
        import_id = str(ref.get("import_id") or "")
        ref["resolution_entries"] = list(by_fact.get(import_id, ()))


# ── 상속 조상 맵 (spec 111) ─────────────────────────────────


def _ancestor_map(
    modules: list[Module],
) -> dict[str, list[str]]:
    """module_id → every same-file internal ancestor (nearest-first, cycle-safe).

    모듈 너머 부모는 build/import environment 증거 없이 이름으로 해소하지 않는다.
    복수 후보·미발견은 포기한다. C 등 명단 밖 언어는 자연히 빈 맵이다.
    Parent syntax is owned by Parser listener AST fields; Analyzer never reparses a source line.
    """
    by_name_in_file: dict[tuple[str, str], list[str]] = defaultdict(list)
    lang_by_id: dict[str, str] = {}
    for m in modules:
        by_name_in_file[(m.file_path, m.name)].append(m.id)
        lang_by_id[m.id] = lang_of(Path(m.file_path).suffix)

    parents: dict[str, list[str]] = {}
    for m in modules:
        lang = lang_of(Path(m.file_path).suffix)
        profile = reference_inheritance_profile(lang)
        if profile is None:
            continue
        resolved: list[str] = []
        for name in _parent_names(m, lang, profile):
            same_file = by_name_in_file.get((m.file_path, name), [])
            if len(same_file) == 1:
                resolved.append(same_file[0])
                continue
        if resolved:
            parents[m.id] = resolved

    ancestors: dict[str, list[str]] = {}
    python_mro_cache: dict[str, tuple[str, ...]] = {}
    for mid in parents:
        if lang_by_id.get(mid) == "python":
            ancestors[mid] = list(
                _python_c3_linearization(
                    mid, parents, python_mro_cache, visiting=set(),
                )[1:]
            )
            continue
        chain: list[str] = []
        frontier = list(parents.get(mid, []))
        seen = {mid}
        while frontier:
            pid = frontier.pop(0)
            if pid in seen:
                continue
            seen.add(pid)
            chain.append(pid)
            frontier.extend(parents.get(pid, []))
        if chain:
            ancestors[mid] = chain
    return ancestors


def _python_c3_linearization(
    module_id: str,
    parents: dict[str, list[str]],
    memo: dict[str, tuple[str, ...]],
    *,
    visiting: set[str],
) -> tuple[str, ...]:
    """Return Python's exact local C3 MRO; invalid/cyclic hierarchies fail closed."""
    cached = memo.get(module_id)
    if cached is not None:
        return cached
    if module_id in visiting:
        raise ValueError(f"cyclic Python inheritance at {module_id!r}")
    direct = list(parents.get(module_id, ()))
    if not direct:
        result = (module_id,)
        memo[module_id] = result
        return result

    active = visiting | {module_id}
    sequences = [
        list(_python_c3_linearization(parent, parents, memo, visiting=active))
        for parent in direct
    ]
    sequences.append(list(direct))
    merged: list[str] = []
    while any(sequences):
        sequences = [sequence for sequence in sequences if sequence]
        candidate = next((
            sequence[0]
            for sequence in sequences
            if all(sequence[0] not in other[1:] for other in sequences)
        ), None)
        if candidate is None:
            raise ValueError(
                f"inconsistent Python C3 inheritance at {module_id!r}: {direct!r}"
            )
        merged.append(candidate)
        for sequence in sequences:
            if sequence and sequence[0] == candidate:
                sequence.pop(0)
    result = (module_id, *merged)
    memo[module_id] = result
    return result


def _parent_names(module: Module, lang: str, profile: dict) -> list[str]:
    """Normalize only listener-emitted type names; dynamic expressions stay unresolved."""
    separator = str(profile.get("parent_separator") or ",")
    owner_separator = str(profile.get("separator") or ".")
    generic_open = str(profile.get("generic_open") or "")
    ignored = {str(name) for name in (profile.get("ignored_parents") or ())}
    identifier = str(source_lexicon(lang)["identifier_pattern"])
    qualified = re.compile(
        rf"{identifier}(?:\s*{re.escape(owner_separator)}\s*{identifier})*"
    )
    names: list[str] = []
    for field_name in profile["ast_fields"]:
        raw_value = str(getattr(module, str(field_name), "") or "")
        for raw_name in _split_top_level(raw_value, separator):
            candidate = raw_name.strip()
            if not candidate or _has_top_level(candidate, "="):
                continue
            if generic_open:
                generic_index = _first_top_level(candidate, generic_open)
                if generic_index >= 0:
                    candidate = candidate[:generic_index].strip()
            if not qualified.fullmatch(candidate):
                continue
            name = candidate.rsplit(owner_separator, 1)[-1].strip()
            if name and name not in ignored and name not in names:
                names.append(name)
    return names


def _split_top_level(value: str, separator: str) -> list[str]:
    """Split one parser-owned type list without breaking generic/call arguments."""
    if not value:
        return []
    if len(separator) != 1:
        raise ValueError("inheritance parent_separator must be one character")
    parts: list[str] = []
    start = 0
    stack: list[str] = []
    quote = ""
    escaped = False
    pairs = {"(": ")", "[": "]", "{": "}", "<": ">"}
    for index, char in enumerate(value):
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = ""
            continue
        if char in {"'", '"'}:
            quote = char
        elif char in pairs:
            stack.append(pairs[char])
        elif stack and char == stack[-1]:
            stack.pop()
        elif char == separator and not stack:
            parts.append(value[start:index])
            start = index + 1
    parts.append(value[start:])
    return parts


def _first_top_level(value: str, token: str) -> int:
    """Return a top-level token position while respecting earlier nested groups."""
    stack: list[str] = []
    pairs = {"(": ")", "[": "]", "{": "}", "<": ">"}
    for index, char in enumerate(value):
        if char == token and not stack:
            return index
        if char in pairs:
            stack.append(pairs[char])
        elif stack and char == stack[-1]:
            stack.pop()
    return -1


def _has_top_level(value: str, token: str) -> bool:
    return _first_top_level(value, token) >= 0


# ── 인덱스 빌드 ──────────────────────────────────────────────

async def _link_code_bodies(
    code_nodes: list[CodeNode], targets: _Targets,
    cache: SourceFileCache,
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
        if not chain:
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
    targets: _Targets,
    checkpoint: Callable[[], Awaitable[None]] | None,
    ancestors: dict[str, list[str]] | None = None,
) -> list[InitBy]:
    """변수/상수 ``initValue`` 의 식별자 → 같은 모듈/상속 출처 INIT_BY.

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
        seen: set[str] = {from_id}                       # 자기참조 제외
        hits = _chain_hits(text, chain, seen, identifier_re)
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
