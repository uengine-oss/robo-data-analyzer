"""검색 목록과 상세 조회가 공유하는 RULE/EXAMPLE/TABLE 근거 읽기.

RULE은 statement 해시로 함수 간 공유되므로 Example id의 함수 prefix로 소유 범위를
반드시 제한한다. MCP 목록과 node_detail이 같은 조립 규칙을 사용하도록 이 모듈만 진실로 둔다.
"""
from __future__ import annotations

from graph.schema.rel_types import HAS_COLUMN
from graph.ownership import ANALYZER_GRAPH_OWNER


# 관계 그대로 펼쳐 받고(규칙×예시×테이블 1행) 소유 판정·묶음은 파이썬이 한다. 예전엔 질의 안에서
# ``e.id STARTS WITH hr.occurrence_id + '::'``·``collect(DISTINCT {map})`` 로 했는데 Ontological DB 는
# 속성과 문자열의 ``+`` 를 ``operator does not exist: text + unknown`` 으로 거절한다(실측).
_RULE_ROWS_QUERY = (
    "MATCH (x {id:$id, graph_owner:$graph_owner})-[hr:HAS_RULE]->"
    "(r:RULE {graph_owner:$graph_owner}) "
    "OPTIONAL MATCH (r)-[:HAS_EXAMPLE]->(e:EXAMPLE {graph_owner:$graph_owner}) "
    "OPTIONAL MATCH (e)-[at:AFFECTS_TABLE]->(t:TABLE {graph_owner:$graph_owner}) "
    "RETURN r.id AS rule_id, hr.occurrence_id AS occurrence_id, "
    "  coalesce(hr.ordinal,0) AS ordinal, r.statement AS statement, e.id AS example_id, "
    "  e.given AS given, e.when_ AS when_, e.then_ AS then_, t.name AS table_name, at.op AS op"
)


def owned_rule_rows(raw_rows: list[dict], node_id: str) -> list[dict]:
    """펼친 행 → (HAS_RULE, 소유 EXAMPLE) 당 1행 + tables. 옛 질의와 같은 소유 규칙·정렬.

    RULE 은 statement 해시로 함수 간 공유되므로 EXAMPLE 은 이 출현(occurrence_id) — 없으면 이
    함수(``<node_id>::``) — 의 것만 인정한다. 인정된 예시가 하나도 없는 규칙은 예시 없이 1행.
    """
    prefix = f"{node_id}::"
    groups: dict[tuple, dict] = {}
    for row in raw_rows:
        occurrence_id = row.get("occurrence_id") or ""
        rule_key = (row.get("rule_id"), occurrence_id, row.get("ordinal") or 0)
        example_id = row.get("example_id")
        owner = f"{occurrence_id}::" if occurrence_id.strip() else prefix
        owned = example_id is not None and str(example_id).startswith(owner)
        key = (*rule_key, example_id if owned else None)
        group = groups.get(key)
        if group is None:
            group = {
                "rule_id": row.get("rule_id"),
                "occurrence_id": row.get("occurrence_id"),
                "ordinal": row.get("ordinal") or 0,
                "statement": row.get("statement"),
                "example_id": example_id if owned else None,
                "given": row.get("given") if owned else None,
                "when_": row.get("when_") if owned else None,
                "then_": row.get("then_") if owned else None,
                "tables": [],
            }
            groups[key] = group
        if owned and row.get("table_name") is not None:
            table = {"name": row.get("table_name"), "op": row.get("op")}
            if table not in group["tables"]:
                group["tables"].append(table)
    with_examples = {key[:3] for key in groups if key[3] is not None}
    rows = [
        group for key, group in groups.items()
        if key[3] is not None or key[:3] not in with_examples
    ]
    rows.sort(key=lambda row: (
        row["ordinal"], row["statement"] or "",
        row["example_id"] is None, len(row["example_id"] or ""), row["example_id"] or "",
    ))
    return rows


def read_rules(session, node_id: str) -> list[dict]:
    """코드 노드의 RULE → 소유 EXAMPLE → AFFECTS_TABLE 구조를 순서 보존해 반환한다."""
    rows = owned_rule_rows(
        session.run(_RULE_ROWS_QUERY, id=node_id, graph_owner=ANALYZER_GRAPH_OWNER).data(),
        node_id,
    )
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
