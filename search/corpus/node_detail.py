"""MCP ``node_detail``용 단일 노드 원본·관계·근거 조회.

검색 랭킹과 무관한 읽기 전용 경계다. 공개 필드 정책과 소스 완전성 검사를 한 곳에서
적용해 API가 Cypher/그래프 세부를 소유하지 않게 한다.
"""
from __future__ import annotations

from typing import Any

from search.corpus.evidence_reader import read_columns, read_rules
from search.corpus.neo4j_reader import read_driver
from graph.ownership import ANALYZER_GRAPH_OWNER

_INTERNAL_PROPERTIES = {"embedding", "search_text", "graph_owner", "element_id"}
_SOURCE_PROPERTIES = {"file_path", "start_line", "end_line", "code_text"}
_CODE_LABELS = {"FUNCTION", "PROCEDURE", "METHOD", "TRIGGER"}


def _error(code: str, message: str) -> dict:
    return {"error": {"code": code, "message": message}}


def _public_properties(properties: dict[str, Any]) -> dict[str, Any]:
    hidden = _INTERNAL_PROPERTIES | _SOURCE_PROPERTIES | {"id", "name"}
    return {key: value for key, value in properties.items() if key not in hidden}


def build_node_detail(
    node_rows: list[dict],
    relationships: list[dict],
    rules: list[dict],
    columns: list[dict],
    *,
    node_id: str | None = None,
) -> dict:
    """조회 행을 공개 계약으로 직렬화한다. 테스트와 live reader가 같은 검사를 쓴다."""
    requested_id = (node_id or "").strip()
    if not requested_id and node_rows:
        requested_id = str((node_rows[0].get("props") or {}).get("id") or "").strip()
    if not requested_id:
        return _error("EMPTY_ID", "node_id is required")
    if not node_rows:
        return _error("NODE_NOT_FOUND", "node not found")
    if len(node_rows) != 1:
        return _error("NODE_ID_NOT_UNIQUE", "node id is not unique")

    row = node_rows[0]
    labels = sorted(set(row.get("labels") or []))
    props = dict(row.get("props") or {})
    actual_id = str(props.get("id") or "")
    if actual_id != requested_id:
        return _error("NODE_ID_MISMATCH", "queried node id does not match result")

    is_code = bool(_CODE_LABELS & set(labels))
    source_values = {key: props.get(key) for key in _SOURCE_PROPERTIES}
    if is_code:
        complete = (
            bool(source_values["file_path"])
            and isinstance(source_values["start_line"], int)
            and source_values["start_line"] > 0
            and isinstance(source_values["end_line"], int)
            and source_values["end_line"] >= source_values["start_line"]
            and bool(source_values["code_text"])
        )
        if not complete:
            return _error("SOURCE_INCOMPLETE", "code node source coordinates or text are incomplete")
        source = {"available": True, **source_values}
    else:
        source = {
            "available": False,
            "file_path": None,
            "start_line": None,
            "end_line": None,
            "code_text": None,
        }

    public_relationships = []
    for relationship in relationships:
        public_relationships.append({
            "type": relationship.get("type") or "",
            "direction": relationship.get("direction") or "",
            "node": {
                "id": relationship.get("other_id") or "",
                "name": relationship.get("other_name") or "",
                "labels": sorted(set(relationship.get("other_labels") or [])),
            },
            "properties": {
                key: value for key, value in (relationship.get("properties") or {}).items()
                if key not in _INTERNAL_PROPERTIES
            },
        })

    return {"node": {
        "id": actual_id,
        "labels": labels,
        "name": props.get("name") or "",
        "properties": _public_properties(props),
        "source": source,
        "rules": rules,
        "columns": columns,
        "relationships": public_relationships,
    }}


def read_node_detail(node_id: str) -> dict:
    """현재 요청 DB에서 node id 하나를 읽는다. 부재·중복·불완전 소스는 명시 오류다."""
    requested_id = (node_id or "").strip()
    if not requested_id:
        return _error("EMPTY_ID", "node_id is required")

    driver, database = read_driver()
    try:
        with driver.session(database=database) as session:
            node_rows = session.run(
                "MATCH (n {id:$id, graph_owner:$graph_owner}) "
                "RETURN labels(n) AS labels, properties(n) AS props",
                id=requested_id,
                graph_owner=ANALYZER_GRAPH_OWNER,
            ).data()
            if len(node_rows) != 1:
                return build_node_detail(node_rows, [], [], [], node_id=requested_id)

            relationships = session.run(
                "MATCH (n {id:$id, graph_owner:$graph_owner})-[r]-"
                "(other {graph_owner:$graph_owner}) "
                "RETURN type(r) AS type, "
                "CASE WHEN startNode(r) = n THEN 'out' ELSE 'in' END AS direction, "
                "coalesce(other.id,'') AS other_id, coalesce(other.name,'') AS other_name, "
                "labels(other) AS other_labels, properties(r) AS properties "
                "ORDER BY type, direction, other_id",
                id=requested_id,
                graph_owner=ANALYZER_GRAPH_OWNER,
            ).data()
            labels = set(node_rows[0].get("labels") or [])
            rules = read_rules(session, requested_id)
            columns = read_columns(session, requested_id) if "TABLE" in labels else []
            return build_node_detail(
                node_rows,
                relationships,
                rules,
                columns,
                node_id=requested_id,
            )
    finally:
        driver.close()
