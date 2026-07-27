"""검색 목록과 상세 조회가 공유하는 RULE/EXAMPLE/TABLE 근거 읽기.

RULE은 statement 해시로 함수 간 공유되므로 Example id의 함수 prefix로 소유 범위를
반드시 제한한다. MCP 목록과 node_detail이 같은 조립 규칙을 사용하도록 이 모듈만 진실로 둔다.
"""
from __future__ import annotations

from graph.schema.rel_types import HAS_COLUMN
from graph.ownership import ANALYZER_GRAPH_OWNER


def read_rules(session, node_id: str) -> list[dict]:
    """코드 노드의 RULE → 소유 EXAMPLE → AFFECTS_TABLE 구조를 순서 보존해 반환한다."""
    rows = session.run(
        "MATCH (x {id:$id, graph_owner:$graph_owner})-[hr:HAS_RULE]->"
        "(r:RULE {graph_owner:$graph_owner}) "
        "OPTIONAL MATCH (r)-[:HAS_EXAMPLE]->(e:EXAMPLE {graph_owner:$graph_owner}) "
        "WHERE CASE WHEN trim(coalesce(hr.occurrence_id,'')) <> '' "
        "  THEN e.id STARTS WITH hr.occurrence_id + '::' "
        "  ELSE e.id STARTS WITH $prefix END "
        "OPTIONAL MATCH (e)-[at:AFFECTS_TABLE]->(t:TABLE {graph_owner:$graph_owner}) "
        "WITH hr, r, e, collect(DISTINCT CASE WHEN t IS NULL THEN NULL "
        "  ELSE {name: t.name, op: at.op} END) AS tbs "
        "RETURN r.id AS rule_id, hr.occurrence_id AS occurrence_id, "
        "  coalesce(hr.ordinal,0) AS ordinal, r.statement AS statement, e.id AS example_id, "
        "  e.given AS given, e.when_ AS when_, e.then_ AS then_, "
        "  [x IN tbs WHERE x IS NOT NULL] AS tables "
        "ORDER BY ordinal, r.statement, size(e.id), e.id",
        id=node_id,
        prefix=f"{node_id}::",
        graph_owner=ANALYZER_GRAPH_OWNER,
    ).data()
    rules: list[dict] = []
    by_rule: dict[str, dict] = {}
    example_ids: dict[str, set[str]] = {}
    for row in rows:
        occurrence_id = row.get("occurrence_id") or ""
        group_key = occurrence_id or row["rule_id"]
        rule = by_rule.get(group_key)
        if rule is None:
            rule = {"statement": row.get("statement") or "", "examples": []}
            by_rule[group_key] = rule
            example_ids[group_key] = set()
            rules.append(rule)
        example_id = row.get("example_id")
        if example_id is None or example_id in example_ids[group_key]:
            continue
        example_ids[group_key].add(example_id)
        rule["examples"].append({
            "given": row.get("given") or "",
            "when": row.get("when_") or "",
            "then": row.get("then_") or "",
            "tables": [
                {"name": table["name"], "op": table.get("op") or ""}
                for table in (row.get("tables") or []) if table.get("name")
            ],
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
        "CASE WHEN trim(coalesce(c.description,'')) <> '' THEN c.description "
        "WHEN trim(coalesce(c.analyzed_description,'')) <> '' THEN c.analyzed_description "
        "ELSE '' END AS description "
        "ORDER BY c.id",
        id=table_id,
        graph_owner=ANALYZER_GRAPH_OWNER,
    ).data()
