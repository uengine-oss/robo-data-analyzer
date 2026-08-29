"""Read current published Rule, Example, Table, and Column products.

Rule/Example ownership and source order come only from HAS_RULE/HAS_EXAMPLE.
Routine-to-table context comes from the published READS/WRITES relationships;
the internal AFFECTS_TABLE transport is deliberately not a product relation.
"""
from __future__ import annotations

from graph.schema.rel_types import HAS_COLUMN


def compact_rules(rules: list[dict]) -> list[dict]:
    """Return the minimal Rule projection used in search result lists."""
    out: list[dict] = []
    seen: set[tuple[int, str]] = set()
    for rule in rules or []:
        line = int(rule.get("anchor_line") or 0)
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
        line = int(rule.get("anchor_line") or 0)
        if line <= 0:
            continue
        rule_id = str(rule.get("rule_id") or f"R-{line}")
        key = (line, rule_id)
        if key in seen:
            continue
        seen.add(key)
        out.append({
            "line": line,
            "structural_condition": str(rule.get("structural_condition") or ""),
            "structural_effects": list(rule.get("structural_effects") or []),
            "business_effects": list(rule.get("business_effects") or []),
            "examples": list(rule.get("examples") or []),
        })
    return out


def render_rule_structure(rule: dict) -> str:
    """Render accepted business meaning, otherwise its explicit structural facts."""
    condition = " ".join(str(rule.get("structural_condition") or "").split())
    effects = [
        " ".join(str(effect).split())
        for effect in (
            rule.get("business_effects") or []
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
    """Return one routine's Rule/Example products in their published order."""
    rows = session.run(
        "MATCH (x:CodeNode {stable_id:$stable_id, analysis_scope_id:$analysis_scope_id, "
        "generation_id:$generation_id})-[hr:HAS_RULE]->"
        "(r:Rule {analysis_scope_id:$analysis_scope_id, generation_id:$generation_id}) "
        "OPTIONAL MATCH (r)-[he:HAS_EXAMPLE]->"
        "(e:Example {analysis_scope_id:$analysis_scope_id, generation_id:$generation_id}) "
        "RETURN r.stable_id AS rule_node_id, "
        "  coalesce(hr.ordinal,0) AS ordinal, r.anchor_line AS anchor_line, "
        "  r.structural_condition AS structural_condition, "
        "  r.structural_effects AS structural_effects, "
        "  r.business_effects AS business_effects, "
        "  coalesce(he.ordinal,0) AS example_ordinal, "
        "  e.stable_id AS example_id, "
        "  e.given_conditions AS given_conditions, "
        "  e.expected_effects AS expected_effects "
        "ORDER BY ordinal, anchor_line, example_ordinal, e.stable_id",
        stable_id=node_id,
    ).data()
    rules: list[dict] = []
    by_rule: dict[str, dict] = {}
    example_ids: dict[str, set[str]] = {}
    for row in rows:
        group_key = row["rule_node_id"]
        rule = by_rule.get(group_key)
        if rule is None:
            rule = {
                "rule_id": f"R-{int(row.get('anchor_line') or 0)}",
                "anchor_line": row.get("anchor_line") or 0,
                "structural_condition": row.get("structural_condition") or "",
                "structural_effects": list(row.get("structural_effects") or []),
                "business_effects": list(row.get("business_effects") or []),
                "examples": [],
            }
            by_rule[group_key] = rule
            example_ids[group_key] = set()
            rules.append(rule)
        example_id = row.get("example_id")
        if example_id is None or example_id in example_ids[group_key]:
            continue
        example_ids[group_key].add(example_id)
        rule["examples"].append({
            "given_conditions": list(row.get("given_conditions") or []),
            "expected_effects": list(row.get("expected_effects") or []),
        })
    return rules


def read_columns(session, table_id: str) -> list[dict]:
    """TABLE의 컬럼을 graph id 순서로 반환한다. 컬럼 미수집은 정상 빈 배열이다."""
    return session.run(
        f"MATCH (t:Table {{stable_id:$stable_id, analysis_scope_id:$analysis_scope_id, "
        f"generation_id:$generation_id}})-[:{HAS_COLUMN}]->"
        "(c:Column {analysis_scope_id:$analysis_scope_id, generation_id:$generation_id}) "
        "RETURN c.stable_id AS id, c.physical_column_name AS name, "
        "coalesce(c.physical_column_name_is_quoted,false) AS name_quoted, "
        "coalesce(c.data_type,'') AS dtype, "
        "coalesce(c.is_primary_key,false) AS is_primary_key, "
        "coalesce(c.logical_name,'') AS logical_name, "
        "coalesce(c.meaning_status,'') AS meaning_status, "
        "coalesce(c.analyzed_description,'') AS description "
        "ORDER BY c.stable_id",
        stable_id=table_id,
    ).data()
