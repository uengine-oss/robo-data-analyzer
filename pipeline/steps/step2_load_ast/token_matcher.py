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
    · Java   : ``import`` FQN → ``소유자.멤버`` 정규식으로 그 모듈 상수 매치.
    · Python : ``import`` / ``from import`` 가 못박은 모듈의 ``모듈.이름``·맨이름 매치.
    · C      : ``#include "헤더"`` 한 헤더 모듈만 후보에 더해 맨이름 매치.
  명단에 없는 언어는 Pass 2 skip (Pass 1 은 그대로) — 안전 degrade + log 1회.

오탐 차단: 주석·문자열 리터럴 제거 후 식별자 토큰 단위 정확 일치. import 로 범위를
좁히므로 동명이인(타 모듈 같은 이름)을 오연결하지 않는다.
※ 못 잡는 것(설계상 범위, silent fail 아님): Python 인스턴스 속성 접근
  (``self._cfg.x`` — 타입 추론 필요), Java 상속 상수 (부모 클래스 상수를 맨이름으로).
"""
from __future__ import annotations

import re
from collections import defaultdict
from typing import Awaitable, Callable, NamedTuple

from graph.schema.graph_edges import InitBy, References
from graph.schema.graph_nodes import Constant, CodeNode, Variable
from shared.files.cache import SourceFileCache

_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_QUALIFIED = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)\.([A-Za-z_][A-Za-z0-9_]*)")
_STRING = re.compile(r'"(?:\\.|[^"\\])*"' + r"|'(?:\\.|[^'\\])*'")
_COMMENT = re.compile(r"/\*.*?\*/|//[^\n]*|#[^\n]*")
_C_QUOTED = re.compile(r'^"([^"]+)"')                       # #include "x.h" — 프로젝트 헤더
_PY_FROM = re.compile(r"from\s+(\.*)([\w.]*)\s+import\s+(.+)", re.S)

# module_id → {식별자 이름: [(node_id, 라벨)]}
_Targets = dict[str, dict[str, list[tuple[str, str]]]]

# Pass 2 미지원 언어 — 언어당 1회만 log (노이즈 차단).

from pipeline.steps.step2_load_ast.import_scopes import (
    _Scope, _Targets, _build_scopes, _file_indexes, _targets_by_module,
)

async def link_references(
    code_nodes: list[CodeNode],
    variables: list[Variable],
    constants: list[Constant],
    import_refs: list[dict],
    source_cache: SourceFileCache | None = None,
    checkpoint: Callable[[], Awaitable[None]] | None = None,
) -> tuple[list[References], list[InitBy]]:
    """본문 → REFERENCES, 변수/상수 ``initValue`` → INIT_BY (같은 모듈 + import 한 모듈).

    INIT_BY 는 변수+상수 모두 출발 가능 — 정의 시 외부 토큰 참조하면 잡힌다.

    ``source_cache`` 는 호출부(step2)가 주입 — 같은 step 안에서 캐시를 **두 벌** 만들면
    파일을 두 번 읽는다 (spec 046 FR-008: 종전엔 여기와 load_ast_step 이 각자 만들었다).
    """
    targets = _targets_by_module(variables, constants)
    modules_by_file, basename_index = _file_indexes(variables, constants)
    scopes = _build_scopes(import_refs, targets, modules_by_file, basename_index)
    if checkpoint is not None:
        await checkpoint()
    return (
        await _link_code_bodies(
            code_nodes, targets, scopes, source_cache or SourceFileCache(), checkpoint,
        ),
        await _link_init_values(variables, constants, targets, scopes, checkpoint),
    )


# ── 인덱스 빌드 ──────────────────────────────────────────────

async def _link_code_bodies(
    code_nodes: list[CodeNode], targets: _Targets,
    scopes: dict[str, _Scope], cache: SourceFileCache,
    checkpoint: Callable[[], Awaitable[None]] | None,
) -> list[References]:
    """본문 라인 스캔 — 같은 모듈(Pass 1) + import 한 모듈(Pass 2) 매칭.

    코드노드당 타겟별 1개 (첫 등장 라인을 evidence 로).
    """
    out: list[References] = []
    for fn in code_nodes:
        if checkpoint is not None:
            await checkpoint()
        own = targets.get(fn.owner_id) or {}
        scope = scopes.get(fn.file_path)
        if not own and scope is None:
            continue
        lines = cache.lines(fn.file_path)
        start = max(fn.start_line - 1, 0)
        seen: set[str] = set()
        for offset, raw in enumerate(lines[start:fn.end_line]):
            text = _strip(raw)
            evidence = start + offset + 1
            for tid, _ in _bare_hits(text, own, seen):
                out.append(References(fn.id, tid, evidence))
            if scope is not None:
                for tid, _ in _pass2_hits(text, scope, targets, seen):
                    out.append(References(fn.id, tid, evidence))
    return out


async def _link_init_values(
    variables: list[Variable], constants: list[Constant],
    targets: _Targets, scopes: dict[str, _Scope],
    checkpoint: Callable[[], Awaitable[None]] | None,
) -> list[InitBy]:
    """변수/상수 ``initValue`` 의 식별자 → 출처 노드 INIT_BY (같은 모듈 + import).

    Variable.init_value / Constant.value 둘 다 antlr 가 통합 출력 — size 표현식
    (``[LEN +1]``) · ``=`` 우변 · 상수 식 모두 포함. reader 는 그 텍스트만 본다.
    """
    out: list[InitBy] = []
    for node, from_id, init_text in _init_iter(variables, constants):
        if checkpoint is not None:
            await checkpoint()
        if not init_text:
            continue
        text = _strip(init_text)
        own = targets.get(node.owner_id) or {}
        scope = scopes.get(node.file_path)
        seen: set[str] = {from_id}                       # 자기참조 제외
        hits = _bare_hits(text, own, seen)
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
) -> list[tuple[str, str]]:
    """텍스트의 식별자 토큰을 ``names`` 테이블과 정확 일치 — 미방문분만."""
    hits: list[tuple[str, str]] = []
    for token in sorted(set(_IDENT.findall(text))):
        hits += _collect(names.get(token, ()), seen)
    return hits


def _pass2_hits(
    text: str, scope: _Scope, targets: _Targets, seen: set[str],
) -> list[tuple[str, str]]:
    """모듈 너머 매칭 — C 는 헤더 범위 맨이름, Java/Python 은 소유권 신호."""
    if scope.lang == "c":
        return _bare_hits(text, scope.c_names, seen)
    hits: list[tuple[str, str]] = []
    for owner, member in _QUALIFIED.findall(text):      # 소유자.멤버
        for mid in scope.owner_map.get(owner, ()):
            hits += _collect(targets.get(mid, {}).get(member, ()), seen)
    if scope.bare_map:                                  # from-import / static import 맨이름
        for token in sorted(set(_IDENT.findall(text))):
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


# ── 유틸 ────────────────────────────────────────────────────

def _strip(text: str) -> str:
    """주석·문자열 리터럴을 공백으로 — 식별자 오탐 차단."""
    return _COMMENT.sub(" ", _STRING.sub(" ", text))
