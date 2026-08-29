"""Read one current product node and project a source-free semantic detail."""
from __future__ import annotations

from typing import Any

from pipeline.languages import ROUTINE_LABELS
from search.corpus.evidence_reader import (
    read_columns,
    read_rules,
    semantic_rules,
)
from search.corpus.generation import (
    CompletedGenerationUnavailable,
    completed_generation_session,
)
from search.corpus.neo4j_reader import read_driver


_INTERNAL_PROPERTIES = {
    "embedding",
    "analysis_scope_id",
    "generation_id",
    "stable_id",
}
_CODE_NODE_LABEL = "CodeNode"
_DATA_OBJECT_LABELS = frozenset({"Table", "Column"})


def _error(code: str, message: str) -> dict:
    return {"error": {"code": code, "message": message}}


def _public_properties(properties: dict[str, Any]) -> dict[str, Any]:
    hidden = _INTERNAL_PROPERTIES | {"name"}
    return {key: value for key, value in properties.items() if key not in hidden}


def _display_name(labels: set[str], properties: dict[str, Any]) -> str:
    if "Table" in labels:
        return str(properties.get("physical_table_name") or "")
    if "Column" in labels:
        return str(properties.get("physical_column_name") or "")
    if "File" in labels:
        return str(properties.get("relative_path") or "")
    return str(properties.get("name") or "")


def build_node_detail(
    node_rows: list[dict],
    relationships: list[dict],
    rules: list[dict],
    columns: list[dict],
    *,
    node_id: str | None = None,
) -> dict:
    """Serialize current product rows without exposing storage bookkeeping."""
    requested_id = (node_id or "").strip()
    if not requested_id and node_rows:
        requested_id = str(
            (node_rows[0].get("props") or {}).get("stable_id") or ""
        ).strip()
    if not requested_id:
        return _error("EMPTY_ID", "node_id is required")
    if not node_rows:
        return _error("NODE_NOT_FOUND", "node not found")
    if len(node_rows) != 1:
        return _error("NODE_ID_NOT_UNIQUE", "node stable_id is not unique")

    row = node_rows[0]
    labels = sorted(set(row.get("labels") or []))
    properties = dict(row.get("props") or {})
    actual_id = str(properties.get("stable_id") or "")
    if actual_id != requested_id:
        return _error("NODE_ID_MISMATCH", "queried stable_id does not match result")

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
            "properties": {
                key: value
                for key, value in (relationship.get("properties") or {}).items()
                if key not in _INTERNAL_PROPERTIES
            },
        })

    return {"node": {
        "id": actual_id,
        "labels": labels,
        "name": _display_name(set(labels), properties),
        "properties": _public_properties(properties),
        "rules": rules,
        "columns": columns,
        "relationships": public_relationships,
    }}


def _valid_artifact_hash(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _function_meaning(properties: dict[str, Any]) -> dict | None:
    status = properties.get("meaning_status")
    count = properties.get("missing_context_count")
    summary = properties.get("summary")
    stereotype = properties.get("stereotype")
    artifact_hash = properties.get("missing_context_artifact_hash")
    if status not in {"sufficient", "partial", "insufficient"}:
        return None
    has_summary = isinstance(summary, str) and bool(summary.strip())
    has_stereotype = isinstance(stereotype, str) and bool(stereotype.strip())
    valid_count = isinstance(count, int) and not isinstance(count, bool) and count >= 0
    valid_presence = (
        valid_count
        and has_summary == has_stereotype
        and (
            (status == "sufficient" and has_summary and count == 0 and not artifact_hash)
            or (
                status == "partial"
                and has_summary
                and count > 0
                and _valid_artifact_hash(artifact_hash)
            )
            or (
                status == "insufficient"
                and not has_summary
                and count > 0
                and _valid_artifact_hash(artifact_hash)
            )
        )
    )
    if not valid_presence:
        return None
    return {
        "summary": summary if has_summary else None,
        "stereotype": stereotype if has_stereotype else None,
        "meaning_status": status,
    }


def _data_object_meaning(properties: dict[str, Any]) -> dict | None:
    status = properties.get("meaning_status")
    count = properties.get("missing_context_count")
    artifact_hash = properties.get("missing_context_artifact_hash")
    logical_name = str(properties.get("logical_name") or "").strip()
    description = str(properties.get("analyzed_description") or "").strip()
    valid_count = isinstance(count, int) and not isinstance(count, bool) and count >= 0
    valid_presence = (
        status in {"sufficient", "insufficient"}
        and valid_count
        and (
            (
                status == "sufficient"
                and logical_name
                and description
                and count == 0
                and not artifact_hash
            )
            or (
                status == "insufficient"
                and not logical_name
                and not description
                and count > 0
                and _valid_artifact_hash(artifact_hash)
            )
        )
    )
    if not valid_presence:
        return None
    return {
        "logical_name": logical_name or None,
        "analyzed_description": description or None,
        "meaning_status": status,
    }


def build_semantic_detail(detail: dict, table_contexts: list[dict]) -> dict:
    """Project one product node into the only supported semantic detail shape."""
    if "error" in detail:
        return detail
    node = detail["node"]
    labels = set(node.get("labels") or ())
    properties = node.get("properties") or {}
    is_function = (
        _CODE_NODE_LABEL in labels
        and str(properties.get("code_kind") or "") in ROUTINE_LABELS
    )
    if is_function:
        meaning = _function_meaning(properties)
        if meaning is None:
            return _error(
                "FUNCTION_MEANING_INVALID",
                "validated function meaning properties are missing or inconsistent",
            )
        meaning_key = "function_meaning"
    elif labels & _DATA_OBJECT_LABELS:
        meaning = _data_object_meaning(properties)
        if meaning is None:
            return _error(
                "DATA_OBJECT_MEANING_INVALID",
                "validated table or column meaning properties are missing or inconsistent",
            )
        meaning_key = "data_object_meaning"
    else:
        return _error(
            "SEMANTIC_DETAIL_UNSUPPORTED",
            "semantic detail is available only for routine, table, and column products",
        )

    callees = []
    for relationship in node.get("relationships") or []:
        if (
            relationship.get("type") != "CALLS"
            or relationship.get("direction") != "out"
        ):
            continue
        other = relationship.get("node") or {}
        other_properties = other.get("properties") or {}
        callees.append({
            "id": other.get("id") or "",
            "name": other.get("name") or "",
            "summary": other_properties.get("summary") or "",
            "stereotype": other_properties.get("stereotype") or "",
            "meaning_status": other_properties.get("meaning_status") or "",
        })

    structured_rules = semantic_rules(node.get("rules") or [])
    return {"schema_version": "semantic-detail/v3", "node": {
        "id": node.get("id") or "",
        "labels": list(node.get("labels") or []),
        "name": node.get("name") or "",
        meaning_key: meaning,
        "rules": structured_rules,
        "linked_context": {
            "callees": callees,
            "data_objects": table_contexts,
        },
    }}


def _table_context(
    session,
    table_id: str,
    name: str,
    access: list[str],
    properties: dict | None = None,
) -> dict:
    props = properties
    if props is None:
        rows = session.run(
            "MATCH (table:Table {stable_id:$stable_id, "
            "analysis_scope_id:$analysis_scope_id, generation_id:$generation_id}) "
            "RETURN properties(table) AS props",
            stable_id=table_id,
        ).data()
        props = dict(rows[0].get("props") or {}) if len(rows) == 1 else {}
    return {
        "id": table_id,
        "name": name or props.get("physical_table_name") or "",
        "access": sorted(set(access)),
        "logical_name": props.get("logical_name") or "",
        "analyzed_description": props.get("analyzed_description") or "",
        "meaning_status": props.get("meaning_status") or "",
        "columns": read_columns(session, table_id),
    }


def _related_table_contexts(session, relationships: list[dict]) -> list[dict]:
    grouped: dict[str, dict] = {}
    for relationship in relationships:
        relationship_type = str(relationship.get("type") or "")
        if relationship_type not in {"READS", "WRITES"}:
            continue
        if "Table" not in set(relationship.get("other_labels") or []):
            continue
        table_id = str(relationship.get("other_id") or "")
        if not table_id:
            continue
        item = grouped.setdefault(
            table_id,
            {"name": relationship.get("other_name") or "", "access": []},
        )
        item["access"].append("READ" if relationship_type == "READS" else "WRITE")
    return [
        _table_context(session, table_id, item["name"], item["access"])
        for table_id, item in sorted(grouped.items())
    ]


def read_node_detail(
    node_id: str,
    *,
    view: str = "frame",
    analysis_scope_id: str | None = None,
) -> dict:
    """Read a routine/table/column from the active completed generation."""
    requested_id = (node_id or "").strip()
    if not requested_id:
        return _error("EMPTY_ID", "node_id is required")
    if view != "frame":
        return _error("INVALID_VIEW", "view must be frame")

    driver, database = read_driver()
    try:
        with completed_generation_session(
            driver,
            database,
            analysis_scope_id=analysis_scope_id,
        ) as session:
            node_rows = session.run(
                "MATCH (node {stable_id:$stable_id, "
                "analysis_scope_id:$analysis_scope_id, generation_id:$generation_id}) "
                "RETURN labels(node) AS labels, properties(node) AS props",
                stable_id=requested_id,
            ).data()
            if len(node_rows) != 1:
                return build_node_detail(
                    node_rows,
                    [],
                    [],
                    [],
                    node_id=requested_id,
                )

            relationships = session.run(
                "MATCH (node {stable_id:$stable_id, "
                "analysis_scope_id:$analysis_scope_id, generation_id:$generation_id})-[rel]-"
                "(other {analysis_scope_id:$analysis_scope_id, "
                "generation_id:$generation_id}) "
                "RETURN type(rel) AS type, "
                "CASE WHEN startNode(rel) = node THEN 'out' ELSE 'in' END AS direction, "
                "coalesce(other.stable_id,'') AS other_id, "
                "coalesce(other.name, other.physical_table_name, "
                "other.physical_column_name, other.relative_path, '') AS other_name, "
                "labels(other) AS other_labels, properties(other) AS other_properties, "
                "properties(rel) AS properties "
                "ORDER BY type, direction, other_id",
                stable_id=requested_id,
            ).data()
            labels = set(node_rows[0].get("labels") or [])
            rules = (
                read_rules(session, requested_id)
                if _CODE_NODE_LABEL in labels
                else []
            )
            columns = (
                read_columns(session, requested_id)
                if "Table" in labels
                else []
            )
            detail = build_node_detail(
                node_rows,
                relationships,
                rules,
                columns,
                node_id=requested_id,
            )
            if "error" in detail:
                return detail
            if "Table" in labels:
                props = dict(node_rows[0].get("props") or {})
                tables = [_table_context(
                    session,
                    requested_id,
                    str(props.get("physical_table_name") or ""),
                    [],
                    properties=props,
                )]
            else:
                tables = _related_table_contexts(session, relationships)
            return build_semantic_detail(detail, tables)
    except CompletedGenerationUnavailable as error:
        return _error("GRAPH_NOT_PUBLISHED", str(error))
    finally:
        driver.close()
