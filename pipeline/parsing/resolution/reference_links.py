"""Bind Parser-owned identifier facts into REFERENCES and INIT_BY edges.

Parser owns lexing and occurrence coordinates. Analyzer only resolves each supplied name
against same-file declarations and Parser-owned inheritance fields; it never reads or
tokenizes source text here.
"""
from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path
from typing import Awaitable, Callable

from graph.schema.graph_edges import InitBy, References
from graph.schema.graph_nodes import Constant, CodeNode, Module, Variable
from pipeline.source_languages import (
    lang_of,
    reference_inheritance_profile,
    source_lexicon,
)

# module_id → {식별자 이름: [(node_id, 라벨)]}
_Targets = dict[str, dict[str, list[tuple[str, str]]]]

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


async def link_references(
    code_nodes: list[CodeNode],
    variables: list[Variable],
    constants: list[Constant],
    checkpoint: Callable[[], Awaitable[None]] | None = None,
    modules: list[Module] = (),
) -> tuple[list[References], list[InitBy]]:
    """Parser identifier facts → REFERENCES and declaration facts → INIT_BY.

    INIT_BY 는 변수+상수 모두 출발 가능 — 정의 시 외부 토큰 참조하면 잡힌다.
    ``modules`` 가 주어지면 같은 파일에서 exact하게 소유된 상속 조상의 필드/상수까지
    맨이름 매칭한다. 모듈 너머 부모는 import 이름으로 추측하지 않는다.

    """
    targets = _targets_by_module(variables, constants)
    ancestors = _ancestor_map(list(modules))
    if checkpoint is not None:
        await checkpoint()
    return (
        await _link_code_facts(code_nodes, targets, checkpoint, ancestors),
        await _link_init_values(variables, constants, targets, checkpoint, ancestors),
    )


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

async def _link_code_facts(
    code_nodes: list[CodeNode], targets: _Targets,
    checkpoint: Callable[[], Awaitable[None]] | None,
    ancestors: dict[str, list[str]] | None = None,
) -> list[References]:
    """Resolve exact Parser occurrences within each code node.

    코드노드당 타겟별 1개 (첫 등장 라인을 evidence 로). 섀도잉: 같은 이름이 자기
    모듈에 있으면 조상 매칭은 건너뜀 (chain 이 own 먼저 시도).
    """
    out: list[References] = []
    for node in code_nodes:
        if checkpoint is not None:
            await checkpoint()
        own = targets.get(node.owner_id) or {}
        chain = _lookup_chain(node.owner_id, own, targets, ancestors)
        if not chain:
            continue
        seen: set[str] = set()
        for name, evidence_line in node.extra.get("_identifier_references", ()):
            for target_id, _ in _chain_hits((name,), chain, seen):
                out.append(References(node.id, target_id, evidence_line))
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
    identifiers: tuple[str, ...] | list[str], chain: list[dict], seen: set[str],
) -> list[tuple[str, str]]:
    """토큰마다 사슬의 **첫 번째로 이름을 가진 dict 만** 매칭 — 자기 모듈 우선 섀도잉."""
    hits: list[tuple[str, str]] = []
    for token in identifiers:
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
    """Resolve declaration-range Parser occurrences into INIT_BY dependencies."""
    out: list[InitBy] = []
    for node in (*variables, *constants):
        if checkpoint is not None:
            await checkpoint()
        own = targets.get(node.owner_id) or {}
        chain = _lookup_chain(node.owner_id, own, targets, ancestors)
        seen: set[str] = {node.id}
        names = tuple(name for name, _line in node._identifier_references)
        hits = _chain_hits(names, chain, seen)
        for source_id, _ in hits:
            out.append(InitBy(node.id, source_id))
    return out


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
