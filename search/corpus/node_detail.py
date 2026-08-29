"""MCP ``node_detail``용 단일 노드 원본·관계·근거 조회.

검색 랭킹과 무관한 읽기 전용 경계다. 공개 필드 정책과 소스 완전성 검사를 한 곳에서
적용해 API가 Cypher/그래프 세부를 소유하지 않게 한다.
"""
from __future__ import annotations

import json
from typing import Any

from search.corpus.evidence_reader import compact_rules, gwt_rules, read_columns, read_rules
from search.corpus.neo4j_reader import read_driver
from search.corpus.generation import (
    CompletedGenerationUnavailable,
    completed_generation_session,
)
from graph.ownership import ANALYZER_GRAPH_OWNER

_INTERNAL_PROPERTIES = {
    "embedding", "graph_owner", "analysis_scope_id", "generation_id", "element_id",
}
_SOURCE_PROPERTIES = {"file_path", "start_line", "end_line", "code_text"}
_CODE_LABELS = {"FUNCTION", "PROCEDURE", "METHOD", "TRIGGER"}


def _error(code: str, message: str) -> dict:
    return {"error": {"code": code, "message": message}}


def _public_properties(properties: dict[str, Any]) -> dict[str, Any]:
    hidden = _INTERNAL_PROPERTIES | _SOURCE_PROPERTIES | {"id", "name"}
    return {key: value for key, value in properties.items() if key not in hidden}


def _semantic_description(properties: dict[str, Any]) -> str:
    """Return persisted source text first, then Analyzer semantic text."""
    source_description = str(properties.get("description") or "").strip()
    if source_description:
        return source_description
    return str(properties.get("analyzed_description") or "").strip()


def build_node_detail(
    node_rows: list[dict],
    relationships: list[dict],
    rules: list[dict],
    columns: list[dict],
    *,
    node_id: str | None = None,
    require_complete_source: bool = True,
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
        if require_complete_source and not complete:
            return _error("SOURCE_INCOMPLETE", "code node source coordinates or text are incomplete")
        source = {"available": complete, **source_values}
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
        other_properties = dict(relationship.get("other_properties") or {})
        related_source = None
        if relationship.get("type") == "REFERENCES":
            related_source = {
                key: other_properties.get(key) for key in _SOURCE_PROPERTIES
            }
        public_relationships.append({
            "type": relationship.get("type") or "",
            "direction": relationship.get("direction") or "",
            "node": {
                "id": relationship.get("other_id") or "",
                "name": relationship.get("other_name") or "",
                "labels": sorted(set(relationship.get("other_labels") or [])),
                "properties": _public_properties(other_properties),
                **({"source": related_source} if related_source is not None else {}),
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


def build_gwt_detail(detail: dict, table_contexts: list[dict]) -> dict:
    """Project one product node into a source-free semantic detail packet."""
    if "error" in detail:
        return detail
    node = detail["node"]
    props = node.get("properties") or {}
    calls = []
    symbols = []
    for relationship in node.get("relationships") or []:
        other = relationship.get("node") or {}
        if (relationship.get("type") == "CALLS"
                and relationship.get("direction") == "out"):
            target_id = other.get("id") or ""
            direction = "out"
            calls.append({
                "evidence_id": (
                    f"{node.get('id') or ''}::call::{direction}::{target_id}"
                ),
                "direction": direction,
                "id": target_id,
                "name": other.get("name") or "",
                "labels": list(other.get("labels") or []),
            })
        elif relationship.get("type") == "REFERENCES" and relationship.get("direction") == "out":
            target_id = other.get("id") or ""
            reference_line = (relationship.get("properties") or {}).get("evidence_line")
            symbols.append({
                "evidence_id": (
                    f"{node.get('id') or ''}::symbol::{target_id}::{reference_line or 0}"
                ),
                "relation": "TARGET_REFERENCES_DEFINITION",
                "id": target_id,
                "name": other.get("name") or "",
                "labels": list(other.get("labels") or []),
                "definition": dict(other.get("properties") or {}),
                "reference_line": reference_line,
            })
    is_function = bool(_CODE_LABELS.intersection(node.get("labels") or ()))
    if is_function:
        status = props.get("meaning_status")
        count = props.get("missing_context_count")
        summary = props.get("summary")
        stereotype = props.get("stereotype")
        artifact_hash = props.get("missing_context_artifact_hash")
        if status not in {"sufficient", "partial", "insufficient"}:
            return _error(
                "FUNCTION_MEANING_UNAVAILABLE",
                "validated function meaning status is unavailable",
            )
        has_summary = isinstance(summary, str) and bool(summary.strip())
        has_stereotype = isinstance(stereotype, str) and bool(stereotype.strip())
        valid_count = isinstance(count, int) and not isinstance(count, bool) and count >= 0
        valid_hash = (
            isinstance(artifact_hash, str)
            and len(artifact_hash) == 64
            and all(character in "0123456789abcdef" for character in artifact_hash)
        )
        valid_presence = (
            valid_count
            and has_summary == has_stereotype
            and (
                (status == "sufficient" and has_summary and count == 0 and not artifact_hash)
                or (status == "partial" and has_summary and count > 0 and valid_hash)
                or (status == "insufficient" and not has_summary and count > 0 and valid_hash)
            )
        )
        if not valid_presence:
            return _error(
                "FUNCTION_MEANING_INVALID",
                "function meaning property presence is inconsistent",
            )
        function_meaning = {
            "summary": summary if has_summary else None,
            "stereotype": stereotype if has_stereotype else None,
            "meaning_status": status,
            "missing_context_count": count,
            **(
                {"missing_context_artifact_hash": artifact_hash}
                if artifact_hash else {}
            ),
        }
    else:
        status = props.get("meaning_status")
        count = props.get("missing_context_count")
        artifact_hash = props.get("missing_context_artifact_hash")
        logical_name = str(props.get("logical_name") or "").strip()
        analyzed_description = str(props.get("analyzed_description") or "").strip()
        valid_count = isinstance(count, int) and not isinstance(count, bool) and count >= 0
        valid_hash = (
            isinstance(artifact_hash, str)
            and len(artifact_hash) == 64
            and all(character in "0123456789abcdef" for character in artifact_hash)
        )
        if status not in {"sufficient", "insufficient"}:
            return _error(
                "DATA_OBJECT_MEANING_UNAVAILABLE",
                "validated table or column meaning status is unavailable",
            )
        valid_presence = (
            valid_count
            and (
                (
                    status == "sufficient" and logical_name and analyzed_description
                    and count == 0 and not artifact_hash
                )
                or (
                    status == "insufficient" and not logical_name
                    and not props.get("analyzed_description")
                    and count > 0 and valid_hash
                )
            )
        )
        if not valid_presence:
            return _error(
                "DATA_OBJECT_MEANING_INVALID",
                "table or column meaning property presence is inconsistent",
            )
        data_object_meaning = {
            "logical_name": logical_name or None,
            "analyzed_description": (
                    analyzed_description or None
            ),
            "meaning_status": status,
            "missing_context_count": count,
            **(
                {"missing_context_artifact_hash": artifact_hash}
                if artifact_hash else {}
            ),
        }
        function_meaning = None
    structured_rules = gwt_rules(node.get("rules") or [], node_id=str(node.get("id") or ""))
    return {"schema_version": "semantic-detail/v2", "node": {
        "id": node.get("id") or "",
        "labels": list(node.get("labels") or []),
        "name": node.get("name") or "",
        **(
            {"function_meaning": function_meaning}
            if function_meaning is not None
            else {"data_object_meaning": data_object_meaning}
        ),
        "rule_examples": [
            {
                "anchor_line": rule["line"],
                "examples": rule["examples"],
            }
            for rule in structured_rules if rule.get("examples")
        ],
        "linked_context": {
            "callees": calls,
            "symbols": symbols,
            "data_objects": [
            {
                "evidence_id": (
                    f"{node.get('id') or ''}::table::{table.get('id') or ''}"
                ),
                **table,
            }
            for table in table_contexts
            ],
        },
    }}


def _sample_snapshot(properties: dict, table_id: str) -> dict:
    raw = properties.get("sample_snapshot_json")
    if raw in (None, ""):
        return {"columns": [], "sample_rows": []}
    try:
        value = json.loads(raw)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid sample snapshot for {table_id}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"invalid sample snapshot for {table_id}")
    return {
        "columns": list(value.get("columns") or []),
        "sample_rows": list(value.get("sample_rows") or []),
    }


def _table_context(
    session, table_id: str, name: str, access: list[str], properties: dict | None = None,
) -> dict:
    props = properties
    if props is None:
        rows = session.run(
            "MATCH (t:TABLE {id:$id, graph_owner:$graph_owner, analysis_scope_id:$analysis_scope_id, generation_id:$generation_id}) "
            "RETURN properties(t) AS props",
            id=table_id,
            graph_owner=ANALYZER_GRAPH_OWNER,
        ).data()
        props = dict(rows[0].get("props") or {}) if len(rows) == 1 else {}
    sample = _sample_snapshot(props, table_id)
    columns = read_columns(session, table_id)
    return {
        "id": table_id,
        "name": name or props.get("name") or "",
        "access": sorted(set(access)),
        "meaning_status": props.get("meaning_status") or "",
        "missing_context_count": props.get("missing_context_count") or 0,
        "missing_context_artifact_hash": (
            props.get("missing_context_artifact_hash") or ""
        ),
        "columns": columns,
        "sample": sample,
    }


def _related_table_contexts(session, relationships: list[dict]) -> list[dict]:
    grouped: dict[str, dict] = {}
    for relationship in relationships:
        rel_type = str(relationship.get("type") or "")
        if rel_type not in {"READS", "WRITES"}:
            continue
        if "TABLE" not in set(relationship.get("other_labels") or []):
            continue
        table_id = str(relationship.get("other_id") or "")
        if not table_id:
            continue
        item = grouped.setdefault(table_id, {
            "name": relationship.get("other_name") or "", "access": [],
        })
        item["access"].append("READ" if rel_type == "READS" else "WRITE")
    return [
        _table_context(session, table_id, item["name"], item["access"])
        for table_id, item in sorted(grouped.items())
    ]


def read_node_detail(
    node_id: str,
    *,
    view: str = "full",
    analysis_scope_id: str | None = None,
) -> dict:
    """현재 요청 DB에서 node id 하나를 읽는다. 부재·중복·불완전 소스는 명시 오류다."""
    requested_id = (node_id or "").strip()
    if not requested_id:
        return _error("EMPTY_ID", "node_id is required")

    driver, database = read_driver()
    try:
        with completed_generation_session(
            driver, database, analysis_scope_id=analysis_scope_id,
        ) as session:
            node_rows = session.run(
                "MATCH (n {id:$id, graph_owner:$graph_owner, analysis_scope_id:$analysis_scope_id, generation_id:$generation_id}) "
                "RETURN labels(n) AS labels, properties(n) AS props",
                id=requested_id,
                graph_owner=ANALYZER_GRAPH_OWNER,
            ).data()
            if len(node_rows) != 1:
                return build_node_detail(node_rows, [], [], [], node_id=requested_id)

            relationships = session.run(
                "MATCH (n {id:$id, graph_owner:$graph_owner, analysis_scope_id:$analysis_scope_id, generation_id:$generation_id})-[r]-"
                "(other {graph_owner:$graph_owner, analysis_scope_id:$analysis_scope_id, generation_id:$generation_id}) "
                "RETURN type(r) AS type, "
                "CASE WHEN startNode(r) = n THEN 'out' ELSE 'in' END AS direction, "
                "coalesce(other.id,'') AS other_id, coalesce(other.name,'') AS other_name, "
                "labels(other) AS other_labels, properties(other) AS other_properties, "
                "properties(r) AS properties "
                "ORDER BY type, direction, other_id",
                id=requested_id,
                graph_owner=ANALYZER_GRAPH_OWNER,
            ).data()
            labels = set(node_rows[0].get("labels") or [])
            rules = read_rules(session, requested_id)
            columns = read_columns(session, requested_id) if "TABLE" in labels else []
            detail = build_node_detail(
                node_rows,
                relationships,
                rules,
                columns,
                node_id=requested_id,
                require_complete_source=view == "full",
            )
            if view == "full" or "error" in detail:
                if "error" not in detail:
                    detail["meta"] = {
                        "analysis_scope_id": session.analysis_scope_id,
                        "generation_id": session.generation_id,
                        "quality_status": session.quality_status,
                        "call_resolution": session.call_resolution,
                        "import_resolution": session.import_resolution,
                    }
                return detail
            try:
                if "TABLE" in labels:
                    props = dict(node_rows[0].get("props") or {})
                    tables = [_table_context(
                        session, requested_id, str(props.get("name") or ""),
                        [], properties=props,
                    )]
                else:
                    tables = _related_table_contexts(session, relationships)
            except ValueError as exc:
                return _error("SAMPLE_SNAPSHOT_INVALID", str(exc))
            packet = build_gwt_detail(detail, tables)
            if "error" not in packet:
                packet["meta"] = {
                    "analysis_scope_id": session.analysis_scope_id,
                    "generation_id": session.generation_id,
                    "quality_status": session.quality_status,
                    "call_resolution": session.call_resolution,
                    "import_resolution": session.import_resolution,
                }
            return packet
    except CompletedGenerationUnavailable as error:
        return _error("GRAPH_NOT_PUBLISHED", str(error))
    finally:
        driver.close()
