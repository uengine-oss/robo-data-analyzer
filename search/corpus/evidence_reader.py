"""검색 목록과 상세 조회가 공유하는 RULE/EXAMPLE/TABLE 근거 읽기.

RULE 은 anchor 좌표 정체성(``<function id>::R<anchor>``)이라 함수별 고유다(spec 116).
AFFECTS_TABLE 은 규칙 단위 결정론 귀속. MCP 목록과 node_detail 이 같은 조립 규칙을
사용하도록 이 모듈만 진실로 둔다.
"""
from __future__ import annotations

from graph.schema.rel_types import HAS_COLUMN
from graph.ownership import ANALYZER_GRAPH_OWNER


def compact_rules(rules: list[dict]) -> list[dict]:
    """Architect 선택/GWT가 공유하는 최소 RULE 구조 projection."""
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
            "tables": list(rule.get("tables") or []),
        })
    return out


def gwt_rules(rules: list[dict], *, node_id: str) -> list[dict]:
    """Preserve the analyzed Rule contract for downstream semantic decisions.

    ``compact_rules`` is intentionally a search-list projection.  Architect GWT
    generation is a semantic consumer and receives the parser/validator-backed
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
            "evidence_id": f"{node_id}::{rule_id}",
            "rule_id": rule_id,
            "line": line,
            "structural_condition": str(rule.get("structural_condition") or ""),
            "structural_effects": list(rule.get("structural_effects") or []),
            "business_condition": str(rule.get("business_condition") or ""),
            "business_effects": list(rule.get("business_effects") or []),
            "owner_signature": str(rule.get("owner_signature") or ""),
            "examples": list(rule.get("examples") or []),
            "tables": list(rule.get("tables") or []),
            "source_anchor": {
                "line": line,
                "coordinate_system": "absolute-file-line",
            },
        })
    return out


def render_rule_structure(rule: dict) -> str:
    """Render accepted business meaning, otherwise its explicit structural facts."""
    selected_condition = rule.get("business_condition")
    condition = " ".join(str(selected_condition or "").split())
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
    """코드 노드의 RULE(leaf 계약) → EXAMPLE → 규칙 단위 AFFECTS_TABLE 을 순서 보존 반환."""
    rows = session.run(
        "MATCH (x {id:$id, graph_owner:$graph_owner, analysis_scope_id:$analysis_scope_id, generation_id:$generation_id})-[hr:HAS_RULE]->"
        "(r:RULE {graph_owner:$graph_owner, analysis_scope_id:$analysis_scope_id, generation_id:$generation_id}) "
        "OPTIONAL MATCH (r)-[at:AFFECTS_TABLE]->(t:TABLE {graph_owner:$graph_owner, analysis_scope_id:$analysis_scope_id, generation_id:$generation_id}) "
        "WITH hr, r, collect(DISTINCT CASE WHEN t IS NULL THEN NULL "
        "  ELSE {name: t.name, access: at.access, op: at.op, op_source: at.op_source} END) AS tbs "
        "OPTIONAL MATCH (r)-[:HAS_EXAMPLE]->(e:EXAMPLE {graph_owner:$graph_owner, analysis_scope_id:$analysis_scope_id, generation_id:$generation_id}) "
        "RETURN r.id AS rule_node_id, "
        "  coalesce(hr.ordinal,0) AS ordinal, r.anchor_line AS anchor_line, "
        "  r.structural_condition AS structural_condition, "
        "  r.structural_effects AS structural_effects, "
        "  r.business_condition AS business_condition, "
        "  r.business_effects AS business_effects, "
        "  x.signature AS owner_signature, "
        "  e.id AS example_id, "
        "  e.given_conditions AS given_conditions, "
        "  e.expected_effects AS expected_effects, "
        "  [x IN tbs WHERE x IS NOT NULL] AS tables "
        "ORDER BY ordinal, anchor_line, size(coalesce(e.id,'')), e.id",
        id=node_id,
        graph_owner=ANALYZER_GRAPH_OWNER,
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
                "business_condition": row.get("business_condition") or "",
                "business_effects": list(row.get("business_effects") or []),
                "owner_signature": row.get("owner_signature") or "",
                "tables": [
                    {
                        **{"name": table["name"], "op": table.get("op") or ""},
                        **(
                            {"access": table.get("access")}
                            if table.get("access") else {}
                        ),
                        **(
                            {"op_source": table.get("op_source")}
                            if table.get("op_source") else {}
                        ),
                    }
                    for table in (row.get("tables") or []) if table.get("name")
                ],
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
            "kind": "satisfy",
            "given_conditions": list(row.get("given_conditions") or []),
            "expected_effects": list(row.get("expected_effects") or []),
        })
    return rules


def read_columns(session, table_id: str) -> list[dict]:
    """TABLE의 컬럼을 graph id 순서로 반환한다. 컬럼 미수집은 정상 빈 배열이다."""
    return session.run(
        f"MATCH (t:TABLE {{id:$id, graph_owner:$graph_owner, analysis_scope_id:$analysis_scope_id, generation_id:$generation_id}})-[:{HAS_COLUMN}]->"
        "(c:COLUMN {graph_owner:$graph_owner, analysis_scope_id:$analysis_scope_id, generation_id:$generation_id}) "
        "RETURN c.id AS id, c.name AS name, coalesce(c.name_quoted,false) AS name_quoted, "
        "coalesce(c.dtype,'') AS dtype, "
        "coalesce(c.is_primary_key,false) AS is_primary_key, "
        "coalesce(c.logical_name,'') AS logical_name, "
        "coalesce(c.meaning_status,'') AS meaning_status, "
        "coalesce(c.missing_context_count,0) AS missing_context_count, "
        "coalesce(c.missing_context_artifact_hash,'') AS missing_context_artifact_hash, "
        "CASE WHEN trim(coalesce(c.description,'')) <> '' THEN c.description "
        "WHEN trim(coalesce(c.analyzed_description,'')) <> '' THEN c.analyzed_description "
        "ELSE '' END AS description "
        "ORDER BY c.id",
        id=table_id,
        graph_owner=ANALYZER_GRAPH_OWNER,
    ).data()
