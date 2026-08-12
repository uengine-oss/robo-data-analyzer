"""검색 목록과 상세 조회가 공유하는 RULE/EXAMPLE/TABLE 근거 읽기.

RULE 은 anchor 좌표 정체성(``<function id>::R<anchor>``)이라 함수별 고유다(spec 116).
AFFECTS_TABLE 은 규칙 단위 결정론 귀속. MCP 목록과 node_detail 이 같은 조립 규칙을
사용하도록 이 모듈만 진실로 둔다.
"""
from __future__ import annotations

from graph.schema.rel_types import HAS_COLUMN
from graph.ownership import ANALYZER_GRAPH_OWNER


def compact_rules(rules: list[dict]) -> list[dict]:
    """Architect 선택/GWT가 공유하는 최소 RULE 좌표·의미·테이블 projection."""
    out: list[dict] = []
    seen: set[tuple[int, str]] = set()
    for rule in rules or []:
        line = int(rule.get("anchor_line") or 0)
        text = str(rule.get("nl") or "").strip()
        if line <= 0 or not text:
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
    generation is a semantic consumer and must receive the parser/validator-backed
    condition/effect structure in addition to the model-owned Korean narrative.
    """
    out: list[dict] = []
    seen: set[tuple[int, str]] = set()
    for rule in rules or []:
        line = int(rule.get("anchor_line") or 0)
        narrative = str(rule.get("nl") or "").strip()
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
            "condition": str(rule.get("cond") or ""),
            "effects": list(rule.get("then") or []),
            "when": str(rule.get("when") or ""),
            "narrative": {
                "text": narrative,
                "authority": "analyzer_llm",
                "role": "human_readable_explanation",
            },
            "examples": list(rule.get("examples") or []),
            "tables": list(rule.get("tables") or []),
            "source_anchor": {
                "line": line,
                "coordinate_system": "absolute-file-line",
            },
        })
    return out


def read_rules(session, node_id: str) -> list[dict]:
    """코드 노드의 RULE(leaf 계약) → EXAMPLE → 규칙 단위 AFFECTS_TABLE 을 순서 보존 반환."""
    rows = session.run(
        "MATCH (x {id:$id, graph_owner:$graph_owner})-[hr:HAS_RULE]->"
        "(r:RULE {graph_owner:$graph_owner}) "
        "OPTIONAL MATCH (r)-[at:AFFECTS_TABLE]->(t:TABLE {graph_owner:$graph_owner}) "
        "WITH hr, r, collect(DISTINCT CASE WHEN t IS NULL THEN NULL "
        "  ELSE {name: t.name, access: at.access, op: at.op, op_source: at.op_source} END) AS tbs "
        "OPTIONAL MATCH (r)-[:HAS_EXAMPLE]->(e:EXAMPLE {graph_owner:$graph_owner}) "
        "RETURN r.id AS rule_node_id, r.rule_id AS rule_id, "
        "  coalesce(hr.ordinal,0) AS ordinal, r.anchor_line AS anchor_line, "
        "  r.cond AS cond, r.then AS effects, r.nl AS nl, r.when AS when, "
        "  e.id AS example_id, e.kind AS kind, e.given AS given, e.then AS example_effects, "
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
                "rule_id": row.get("rule_id") or "",
                "anchor_line": row.get("anchor_line") or 0,
                "cond": row.get("cond") or "",
                "then": list(row.get("effects") or []),
                "nl": row.get("nl") or "",
                "when": row.get("when") or "",
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
            "kind": row.get("kind") or "",
            "given": list(row.get("given") or []),
            "then": list(row.get("example_effects") or []),
        })
    return rules


def read_columns(session, table_id: str) -> list[dict]:
    """TABLE의 컬럼을 graph id 순서로 반환한다. 컬럼 미수집은 정상 빈 배열이다."""
    return session.run(
        f"MATCH (t:TABLE {{id:$id, graph_owner:$graph_owner}})-[:{HAS_COLUMN}]->"
        "(c:COLUMN {graph_owner:$graph_owner}) "
        "RETURN c.id AS id, c.name AS name, coalesce(c.dtype,'') AS dtype, "
        "coalesce(c.is_primary_key,false) AS is_primary_key, "
        "coalesce(c.logical_name,'') AS logical_name, "
        "coalesce(c.semantic_evidence_status,'') AS evidence_status, "
        "coalesce(c.semantic_missing_context,[]) AS missing_context, "
        "coalesce(c.semantic_frame_json,'') AS semantic_frame_json, "
        "CASE WHEN trim(coalesce(c.description,'')) <> '' THEN c.description "
        "WHEN trim(coalesce(c.analyzed_description,'')) <> '' THEN c.analyzed_description "
        "ELSE '' END AS description "
        "ORDER BY c.id",
        id=table_id,
        graph_owner=ANALYZER_GRAPH_OWNER,
    ).data()
