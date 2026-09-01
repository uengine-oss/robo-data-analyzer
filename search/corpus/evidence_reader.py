"""Read current Rule and Column products."""
from __future__ import annotations

from graph.schema.rel_types import HAS_COLUMN


def compact_rules(rules: list[dict]) -> list[dict]:
    """Return the minimal Rule projection used in search result lists."""
    out: list[dict] = []
    seen: set[tuple[int, str]] = set()
    for rule in rules or []:
        line = int(rule.get("line") or 0)
        text = render_rule_structure(rule)
        if line <= 0:
            continue
        key = (line, text)
        if key in seen:
            continue
        seen.add(key)
        out.append({
            "line": line,
            "text": text,
        })
    return out


def semantic_rules(rules: list[dict]) -> list[dict]:
    """Preserve the analyzed Rule contract for downstream semantic decisions.

    ``compact_rules`` is intentionally a search-list projection.  Semantic consumers
    receive the parser/validator-backed
    condition/effect structure plus an interpretation-free rendering of those facts.
    """
    out: list[dict] = []
    seen: set[tuple[int, str]] = set()
    for rule in rules or []:
        line = int(rule.get("line") or 0)
        if line <= 0:
            continue
        rule_id = str(rule.get("rule_id") or f"R-{line}")
        key = (line, rule_id)
        if key in seen:
            continue
        seen.add(key)
        out.append({
            "line": line,
            "condition": str(rule.get("condition") or ""),
            "effects": list(rule.get("effects") or []),
            "condition_description": str(
                rule.get("condition_description") or ""
            ),
            "effect_descriptions": list(
                rule.get("effect_descriptions") or []
            ),
        })
    return out


def render_rule_structure(rule: dict) -> str:
    """Render accepted business meaning, otherwise its explicit structural facts."""
    condition = " ".join(str(rule.get("condition") or "").split())
    effects = [
        " ".join(str(effect).split())
        for effect in (
            rule.get("effect_descriptions") or rule.get("effects") or []
        )
        if str(effect).strip()
    ]
    if condition and effects:
        return f"조건: {condition} → 효과: {'; '.join(effects)}"
    if condition:
        return f"조건: {condition}"
    if effects:
        return f"효과: {'; '.join(effects)}"
    return "구조: 직접 조건·효과 없음"


def read_rules(session, node_id: str) -> list[dict]:
    """Return one routine's Rule products in their published order."""
    rows = session.run(
        "MATCH (x {_id:$node_id, _owner:'analyzer'})-[hr:HAS_RULE]->"
        "(r:RULE {_owner:'analyzer'}) "
        "RETURN r._id AS rule_id, coalesce(hr.order,0) AS rule_order, "
        "r.line AS line, coalesce(r.condition,'') AS condition, "
        "coalesce(r.effects,[]) AS effects, "
        "coalesce(r.condition_description,'') AS condition_description, "
        "coalesce(r.effect_descriptions,[]) AS effect_descriptions "
        "ORDER BY rule_order, line, rule_id",
        node_id=node_id,
    ).data()
    return [dict(row) for row in rows]


def read_columns(session, table_id: str) -> list[dict]:
    """TABLE의 컬럼을 graph id 순서로 반환한다. 컬럼 미수집은 정상 빈 배열이다."""
    return session.run(
        f"MATCH (t:TABLE {{_id:$node_id, _owner:'analyzer'}})-[:{HAS_COLUMN}]->"
        "(c:COLUMN {_owner:'analyzer'}) "
        "RETURN c._id AS id, c.name AS name, "
        "coalesce(c.data_type,'') AS dtype, "
        "coalesce(c.primary_key,false) AS is_primary_key, "
        "coalesce(c.logical_name,'') AS logical_name, "
        "coalesce(c.summary, c.description, '') AS description "
        "ORDER BY c._id",
        node_id=table_id,
    ).data()
