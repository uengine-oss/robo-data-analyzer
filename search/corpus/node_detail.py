"""Read one directly published product node and its nearby evidence."""
from __future__ import annotations

from typing import Any

from pipeline.source_languages import ROUTINE_LABELS
from search.corpus.evidence_reader import read_columns, read_rules, semantic_rules
from search.corpus.neo4j_reader import read_driver


_INTERNAL_PROPERTIES = {"_id", "_owner", "_search_embedding"}


def _error(code: str, message: str) -> dict:
    return {"error": {"code": code, "message": message}}


def _public_properties(properties: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value for key, value in properties.items()
        if key not in _INTERNAL_PROPERTIES
    }


def build_node_detail(
    node_rows: list[dict],
    relationships: list[dict],
    rules: list[dict],
    columns: list[dict],
    *,
    node_id: str,
) -> dict:
    if not node_id.strip():
        return _error("EMPTY_ID", "node_id is required")
    if not node_rows:
        return _error("NODE_NOT_FOUND", "node not found")
    if len(node_rows) != 1:
        return _error("NODE_ID_NOT_UNIQUE", "node _id is not unique")

    row = node_rows[0]
    labels = sorted(set(row.get("labels") or []))
    properties = dict(row.get("props") or {})
    if str(properties.get("_id") or "") != node_id:
        return _error("NODE_ID_MISMATCH", "queried _id does not match result")
    public_relationships = []
    for relationship in relationships:
        other_properties = dict(relationship.get("other_properties") or {})
        public_relationships.append({
            "type": relationship.get("type") or "",
            "direction": relationship.get("direction") or "",
            "node": {
                "id": relationship.get("other_id") or "",
                "name": relationship.get("other_name") or "",
                "labels": sorted(set(relationship.get("other_labels") or [])),
                "properties": _public_properties(other_properties),
            },
            "properties": _public_properties(
                dict(relationship.get("properties") or {})
            ),
        })
    return {"node": {
        "id": node_id,
        "labels": labels,
        "name": str(properties.get("name") or ""),
        "properties": _public_properties(properties),
        "rules": rules,
        "columns": columns,
        "relationships": public_relationships,
    }}


def _table_context(session, table_id: str, access: list[str]) -> dict:
    rows = session.run(
        "MATCH (table:TABLE {_id:$node_id, _owner:'analyzer'}) "
        "RETURN properties(table) AS props",
        node_id=table_id,
    ).data()
    props = dict(rows[0].get("props") or {}) if len(rows) == 1 else {}
    return {
        "id": table_id,
        "name": props.get("name") or "",
        "access": sorted(set(access)),
        "logical_name": props.get("logical_name") or "",
        "summary": props.get("summary") or props.get("description") or "",
        "columns": read_columns(session, table_id),
    }


def _related_table_contexts(session, relationships: list[dict]) -> list[dict]:
    grouped: dict[str, list[str]] = {}
    for relationship in relationships:
        relationship_type = str(relationship.get("type") or "")
        if relationship_type not in {"READS", "WRITES"}:
            continue
        if "TABLE" not in set(relationship.get("other_labels") or []):
            continue
        table_id = str(relationship.get("other_id") or "")
        if table_id:
            grouped.setdefault(table_id, []).append(
                "READ" if relationship_type == "READS" else "WRITE"
            )
    return [
        _table_context(session, table_id, access)
        for table_id, access in sorted(grouped.items())
    ]


def build_semantic_detail(detail: dict, table_contexts: list[dict]) -> dict:
    if "error" in detail:
        return detail
    node = detail["node"]
    labels = set(node.get("labels") or ())
    properties = node.get("properties") or {}
    if labels & ROUTINE_LABELS:
        meaning_key = "function_meaning"
        meaning = {
            "summary": properties.get("summary") or "",
            "stereotype": properties.get("stereotype") or "",
        }
    elif "TABLE" in labels or "COLUMN" in labels:
        meaning_key = "data_object_meaning"
        meaning = {
            "logical_name": properties.get("logical_name") or "",
            "summary": properties.get("summary") or properties.get("description") or "",
        }
    else:
        return _error(
            "SEMANTIC_DETAIL_UNSUPPORTED",
            "semantic detail is available only for routine, table, and column nodes",
        )

    callees = []
    for relationship in node.get("relationships") or []:
        if relationship.get("type") != "CALLS" or relationship.get("direction") != "out":
            continue
        other = relationship.get("node") or {}
        other_properties = other.get("properties") or {}
        callees.append({
            "id": other.get("id") or "",
            "name": other.get("name") or "",
            "summary": other_properties.get("summary") or "",
            "stereotype": other_properties.get("stereotype") or "",
        })
    return {"schema_version": "semantic-detail/v4", "node": {
        "id": node.get("id") or "",
        "labels": list(node.get("labels") or []),
        "name": node.get("name") or "",
        "properties": properties,
        meaning_key: meaning,
        "rules": semantic_rules(node.get("rules") or []),
        "linked_context": {"callees": callees, "data_objects": table_contexts},
    }}


def read_node_detail(node_id: str, *, view: str = "frame") -> dict:
    requested_id = (node_id or "").strip()
    if not requested_id:
        return _error("EMPTY_ID", "node_id is required")
    if view != "frame":
        return _error("INVALID_VIEW", "view must be frame")

    driver, database = read_driver()
    try:
        with driver.session(database=database) as session:
            node_rows = session.run(
                "MATCH (node {_id:$node_id, _owner:'analyzer'}) "
                "RETURN labels(node) AS labels, properties(node) AS props",
                node_id=requested_id,
            ).data()
            if len(node_rows) != 1:
                return build_node_detail(
                    node_rows, [], [], [], node_id=requested_id,
                )
            relationships = session.run(
                "MATCH (node {_id:$node_id, _owner:'analyzer'})-[rel]-"
                "(other {_owner:'analyzer'}) "
                "RETURN type(rel) AS type, "
                "CASE WHEN startNode(rel) = node THEN 'out' ELSE 'in' END AS direction, "
                "other._id AS other_id, coalesce(other.name,'') AS other_name, "
                "labels(other) AS other_labels, properties(other) AS other_properties, "
                "properties(rel) AS properties "
                "ORDER BY type, direction, other_id",
                node_id=requested_id,
            ).data()
            labels = set(node_rows[0].get("labels") or [])
            rules = read_rules(session, requested_id) if labels & ROUTINE_LABELS else []
            columns = read_columns(session, requested_id) if "TABLE" in labels else []
            detail = build_node_detail(
                node_rows, relationships, rules, columns, node_id=requested_id,
            )
            tables = (
                [_table_context(session, requested_id, [])]
                if "TABLE" in labels
                else _related_table_contexts(session, relationships)
            )
            return build_semantic_detail(detail, tables)
    finally:
        driver.close()
