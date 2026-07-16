"""골격 그래프 델타 → NDJSON 스트림 emit (공통 헬퍼, 전략 무관·DRY).

각 비-LLM step(DDL·AST·링킹)이 자기 노드/관계를 만든 직후 이 헬퍼로 emit하면,
프론트가 그 step 완료 즉시 트리·그래프·카운트에 라이브로 반영한다. step6의 코드노드
emit과 **같은 wire 형식**(node_event/relationship_event) — 프론트 소비 경로 단일.

설계
────
- 노드 라벨 = ``node.label``(antlr type 보유 종류) 또는 고정 라벨(Table/Column/Package/Schema).
- 노드 props = 빈값 제외 **표시용 속성만**(이름·라인 등). 영속 속성 전체가 아니라 프론트
  표시에 필요한 것만 — step6가 이후 summary/logical_name으로 보강(같은 id → 병합).
- 관계 = ``rel_types`` 상수(단일 진실) + dataclass 끝점 필드(역할명) → from.id/to.id.
- ``stream`` None(CLI 단독)이면 전부 no-op.
"""
from __future__ import annotations

from typing import Iterable, Optional

from graph.schema import graph_edges as ge
from graph.schema import graph_nodes as gn
from log.ndjson_stream import NDJSONStream
from graph.schema import rel_types as rt
from graph.node_upsert import nonempty


# label 필드 없는 노드 종류의 고정 라벨 (나머지는 node.label = antlr type).
_FIXED_NODE_LABEL: dict[type, str] = {
    gn.Table: "TABLE",
    gn.Column: "COLUMN",
    gn.Package: "PACKAGE",
    gn.Schema: "SCHEMA",
}

# 프론트 표시에 쓰는 속성만 emit (caption·트리 라벨). 빈값은 nonempty가 제거.
_DISPLAY_PROP_KEYS = (
    "name", "logical_name", "signature", "summary", "stereotype",
    "file_path", "start_line", "end_line", "owner_id", "dtype",
)

# 관계 dataclass → (rel_type, from 필드, to 필드). ReadsWrites는 relation 필드가 동적 → 별도.
_EDGE_SPEC: dict[type, tuple[str, str, str]] = {
    ge.HasColumn:  (rt.HAS_COLUMN, "parent_id", "child_id"),
    ge.BelongsTo:  (rt.BELONGS_TO, "child_id", "parent_id"),
    ge.Fk:         (rt.FK,         "from_id", "to_id"),
    ge.Calls:      (rt.CALLS,      "caller_id", "callee_id"),
    ge.ParentOf:   (rt.PARENT_OF,  "parent_id", "child_id"),
    ge.HasMember:  (rt.HAS_MEMBER, "parent_id", "child_id"),
    ge.References: (rt.REFERENCES, "source_id", "target_id"),
    ge.InitBy:     (rt.INIT_BY,    "from_id", "source_id"),
}


def _node_label(node) -> Optional[str]:
    return getattr(node, "label", None) or _FIXED_NODE_LABEL.get(type(node))


def _node_labels(node) -> list[str]:
    """node_event 라벨 배열 = [antlr 타입] + (stereotype 있으면) — Neo4j 멀티라벨과 동형.

    위치: labels[0]=타입, labels[1:]=stereotype(spec 033/035 일관). stereotype 은 CodeNode·
    Module 만 가짐(그 외 노드는 getattr 기본 None) → labels=[타입].
    """
    primary = _node_label(node)
    if not primary:
        return []
    stereotype = getattr(node, "stereotype", None)
    return [primary, stereotype] if stereotype else [primary]


def _node_props(node) -> dict:
    return nonempty({k: getattr(node, k, "") for k in _DISPLAY_PROP_KEYS})


def _edge_wire(edge) -> Optional[tuple[str, str, str, dict]]:
    """엣지 → (rel_type, from_id, to_id, props). 미등록 종류면 None."""
    if type(edge) is ge.ReadsWrites:
        return (edge.relation, edge.source_id, edge.target_id,
                nonempty({"dml_type": edge.dml_type}))
    spec = _EDGE_SPEC.get(type(edge))
    if spec is None:
        return None
    rel_type, from_attr, to_attr = spec
    return (rel_type, getattr(edge, from_attr, ""), getattr(edge, to_attr, ""), {})


def emit_nodes(stream: Optional[NDJSONStream], nodes: Iterable) -> int:
    """노드 델타 → node_event. emit한 개수 반환(message 요약용)."""
    if stream is None:
        return 0
    count = 0
    for node in nodes:
        label = _node_label(node)
        nid = getattr(node, "id", None)
        if not label or not nid:
            continue
        stream.emit({
            "type": "node_event", "op": "merge",
            # label = 단일 타입(back-compat=labels[0]). labels = 전체 라벨(타입+stereotype)
            # → 프론트가 역할을 라이브로 받는다(spec 034).
            "label": label, "labels": _node_labels(node), "id": nid, "props": _node_props(node),
        })
        count += 1
    return count


def emit_edges(stream: Optional[NDJSONStream], edges: Iterable) -> int:
    """관계 델타 → relationship_event. emit한 개수 반환."""
    if stream is None:
        return 0
    count = 0
    for edge in edges:
        wire = _edge_wire(edge)
        if wire is None:
            continue
        rel_type, from_id, to_id, props = wire
        if not from_id or not to_id:
            continue
        stream.emit({
            "type": "relationship_event", "op": "merge", "rel_type": rel_type,
            "from": {"id": from_id}, "to": {"id": to_id}, "props": props,
        })
        count += 1
    return count


# (옛 ``emit_graph_delta`` 삭제 — 호출부 0건. 소비처는 ``emit_nodes``/``emit_edges`` 를
#  직접 호출한다. spec 046 FR-010.)


def emit_message(stream: Optional[NDJSONStream], content: str) -> None:
    """콘솔용 진행 message 1건. step 완료 요약 등."""
    if stream is None:
        return
    stream.emit({"type": "message", "content": content})
