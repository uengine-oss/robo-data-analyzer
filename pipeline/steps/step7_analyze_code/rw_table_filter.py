"""Derive RULE table effects from validated leaves and deterministic R/W."""
from __future__ import annotations

from typing import Iterable, Optional

from pipeline.quality_gates.evidence_matching import resolve_qualified_name
from pipeline.quality_gates.example_synthesis import synthesize_examples


_READ_OP = "READ"
_WRITE_OPS = {"INSERT", "UPDATE", "DELETE", "MERGE", "TRUNCATE"}
_EXACT_OPS = {_READ_OP, *_WRITE_OPS}


def _authoritative_op(value: object) -> str | None:
    """Scanner 연산만 폐쇄형 최종값으로 승격한다."""
    op = str(value or "").strip().upper()
    if op == "SELECT":
        op = _READ_OP
    return op if op in _EXACT_OPS else None


_THEN_EFFECT_OPS = {
    "select": ("READ", "READ"),
    "insert": ("WRITE", "INSERT"),
    "update": ("WRITE", "UPDATE"),
    "delete": ("WRITE", "DELETE"),
    "upsert": ("WRITE", "MERGE"),
}


def rule_table_effects(
    then_statements: Iterable[str],
    reads: Iterable[str],
    writes: Iterable[str],
    table_op: Optional[dict[str, str]],
) -> tuple[list[dict], list[str]]:
    """구조화 then 효과에서 규칙 단위 AFFECTS_TABLE transport를 결정론으로 만든다(spec 116).

    LLM 은 테이블 후보를 별도 필드로 내지 않는다 — then 의
    select/insert/update/delete/upsert("테이블") 호출이 유일한 출처이고, 귀속은 결정론
    reads/writes 대조가 확정한다(spec 027 원칙 동일). 반환: (transport 목록,
    미해석 테이블명 목록 — 호출측이 표면화).
    """
    from pipeline.models.rule_expression import parse_effect

    read_exact = {str(t) for t in (reads or ())}
    write_exact = {str(t) for t in (writes or ())}
    authoritative = table_op or {}
    effects: dict[tuple[str, str], dict] = {}
    unresolved: list[str] = []
    for statement in then_statements or ():
        # 이 단계에는 채점 통과 규칙만 온다. parser 실패를 건너뛰면 의미 효과가 조용히
        # 사라지므로 불변식 위반을 그대로 상위에 전파한다.
        call = parse_effect(str(statement))
        mapping = _THEN_EFFECT_OPS.get(call.func.id)
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
        table_id = resolve_qualified_name(name, pool)
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
        op_source = "SCANNER" if authoritative_op else "LLM_INFERRED"
        effects[(table_id, op)] = {
            "table": table_id, "access": access, "op": op, "op_source": op_source,
        }
    return list(effects.values()), unresolved


def serialize_rules(
    rules: Iterable[object],
    when_symbol: str,
    reads: Iterable[str],
    writes: Iterable[str],
    table_op: Optional[dict[str, str]],
) -> tuple[list[dict], list[str]]:
    """채점 통과한 구조화 규칙들 → transport dict (enrichment 는 전부 코드가 채운다).

    rule_id = "R-"+anchor / when = 실행 가능 단위 심볼 / affected_tables = then 결정론
    귀속. 반환: (직렬화 목록, 미해석 테이블명 목록).
    """
    serialized: list[dict] = []
    unresolved_all: list[str] = []
    rules = list(rules or ())
    # 예시는 LLM 산출이 아니라 규칙에서 결정론 합성한다 (spec 116 §21, 헌법 I).
    # 여집합(else_of) 해소에 형제 규칙이 필요하므로 함수·배치 단위로 한 번에 만든다.
    examples_by_anchor = synthesize_examples(rules)
    for rule in rules:
        then = [str(s) for s in (getattr(rule, "then", None) or [])]
        affected, unresolved = rule_table_effects(then, reads, writes, table_op)
        unresolved_all.extend(unresolved)
        serialized.append({
            "rule_id": f"R-{rule.anchor_line}",
            "anchor_line": rule.anchor_line,
            "cond": rule.cond or "",
            "then": then,
            "nl": rule.nl,
            "when": when_symbol,
            "examples": examples_by_anchor.get(rule.anchor_line, []),
            "affected_tables": affected,
        })
    return serialized, unresolved_all
