"""Derive Rule-to-Table access from validated structure and direct R/W."""
from __future__ import annotations

from typing import Iterable, Mapping, Optional

from pipeline.checks.evidence_matching import resolve_qualified_name
_READ_OP = "READ"
_WRITE_OPS = {"INSERT", "UPDATE", "DELETE", "MERGE", "TRUNCATE"}
_EXACT_OPS = {_READ_OP, *_WRITE_OPS}


def _authoritative_op(value: object) -> str | None:
    """Scanner 연산만 폐쇄형 최종값으로 승격한다."""
    op = str(value or "").strip().upper()
    if op == "SELECT":
        op = _READ_OP
    return op if op in _EXACT_OPS else None


_STRUCTURAL_EFFECT_OPS = {
    "select": ("READ", "READ"),
    "insert": ("WRITE", "INSERT"),
    "update": ("WRITE", "UPDATE"),
    "delete": ("WRITE", "DELETE"),
    "upsert": ("WRITE", "MERGE"),
}


def rule_table_accesses(
    structural_effects: Iterable[str],
    reads: Iterable[str],
    writes: Iterable[str],
    table_op: Optional[dict[str, str]],
    table_aliases: Mapping[str, str] | None = None,
) -> tuple[list[dict], list[str]]:
    """구조화 효과에서 규칙 단위 READS/WRITES transport를 결정론으로 만든다.

    LLM 은 테이블 후보를 별도 필드로 내지 않는다 — then 의
    select/insert/update/delete/upsert("테이블") 호출이 유일한 출처이고, 귀속은 결정론
    reads/writes 대조가 확정한다. 반환: (transport 목록,
    미해석 테이블명 목록 — 호출측이 표면화).
    """
    from pipeline.models.rule_expression import parse_effect

    read_exact = {str(t) for t in (reads or ())}
    write_exact = {str(t) for t in (writes or ())}
    authoritative = table_op or {}
    accesses: dict[tuple[str, str], dict] = {}
    unresolved: list[str] = []
    for statement in structural_effects or ():
        # 이 단계에는 채점 통과 규칙만 온다. parser 실패를 건너뛰면 의미 효과가 조용히
        # 사라지므로 불변식 위반을 그대로 상위에 전파한다.
        call = parse_effect(str(statement))
        mapping = _STRUCTURAL_EFFECT_OPS.get(call.func.id)
        if mapping is None or not call.args:
            continue
        first = call.args[0]
        name = first.value if hasattr(first, "value") and isinstance(
            getattr(first, "value", None), str
        ) else None
        if not name:
            continue
        access, derived_op = mapping
        pool = read_exact if access == "READ" else write_exact
        table_id = resolve_qualified_name(name, pool, table_aliases)
        if table_id is None:
            # 모델 효과의 접근 방향과 다른 R/W 집합으로 재귀속하지 않는다.
            unresolved.append(name)
            continue
        # 권위 op(SQL_KEY 유래)가 있으면 그것이 최종이다 — SELECT→READ 정규화 방어 포함.
        authoritative_op = _authoritative_op(authoritative.get(table_id))
        if authoritative_op and authoritative_op != derived_op:
            unresolved.append(name)
            continue
        op = authoritative_op or derived_op
        accesses[(table_id, op)] = {
            "table": table_id, "access": access, "operation": op,
        }
    return list(accesses.values()), unresolved


def serialize_rules(
    rules: Iterable[object],
    reads: Iterable[str],
    writes: Iterable[str],
    table_op: Optional[dict[str, str]],
    table_aliases: Mapping[str, str] | None = None,
) -> tuple[list[dict], list[str]]:
    """채점 통과한 구조화 규칙들 → transport dict (enrichment 는 전부 코드가 채운다).

    Rule identity and owner are derived later from ``anchor_line`` and the owning code
    node. This transport carries only non-reconstructable values and relationships.
    """
    serialized: list[dict] = []
    unresolved_all: list[str] = []
    rules = list(rules or ())
    for rule in rules:
        structural_effects = [str(s) for s in rule.structural_effects]
        table_accesses, unresolved = rule_table_accesses(
            structural_effects, reads, writes, table_op, table_aliases,
        )
        unresolved_all.extend(unresolved)
        serialized.append({
            "anchor_line": rule.anchor_line,
            "structural_condition": rule.structural_condition or "",
            "structural_effects": structural_effects,
            "condition_description": rule.condition_description,
            "effect_descriptions": list(rule.effect_descriptions),
            "table_accesses": table_accesses,
        })
    return serialized, unresolved_all
