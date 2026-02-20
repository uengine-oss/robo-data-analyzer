"""Phase 5.5 (nontarget): Bottom-Up LLM Enrichment — summarise nodes and discover call edges.

LLMClient/Neo4jClient 통합 및 Framework 스키마에 맞춤.
"""

from __future__ import annotations

import asyncio
import re
from typing import Any, AsyncGenerator

from client.llm_client import CustomLLMClient
from client.neo4j_client import Neo4jClient
from config.settings import settings
from util.stream_event import emit_data, emit_message, emit_phase_event, emit_phase_result
from util.text_utils import log_process

from analyzer.strategy.framework.nontarget.data_models import (
    Chunk,
    GraphEdge,
    GraphNode,
    ProjectStructure,
)
from analyzer.strategy.framework.nontarget.phase4_relationships import _map_edge_type_to_framework

# Regex patterns for SQL mapper calls
_SQL_CALL_PATTERN = re.compile(
    r"""(?:selectOne|selectList|insert|update|delete|select"""
    r"""|queryForObject|queryForList)\s*\(\s*"([^"]+)"\s*""",
)

# Field-based method calls: fieldName.methodName(
_METHOD_CALL_PATTERN = re.compile(
    r"""(\w+)\s*\.\s*(\w+)\s*\(""",
)


async def run_phase5_5_enrichment(
    project: ProjectStructure,
    chunks: list[Chunk],
    nodes: list[GraphNode],
    edges: list[GraphEdge],
    client: Neo4jClient,
    llm_client: CustomLLMClient,
    class_index: dict[str, Any] | None = None,
    sql_index: dict[str, Any] | None = None,
) -> AsyncGenerator[bytes, None]:
    """Phase 5.5 (nontarget): Bottom-up LLM 보강
    
    Args:
        project: Phase 1.5에서 생성된 프로젝트 구조
        chunks: Phase 3에서 생성된 청크
        nodes: Phase 5에서 생성된 노드
        edges: Phase 5에서 생성된 관계
        client: Neo4j 클라이언트
        llm_client: LLM 클라이언트
        class_index: 클래스 인덱스
        sql_index: SQL 인덱스
    
    Yields:
        NDJSON 스트리밍 데이터
    """
    # Phase 5.5 시작 이벤트
    yield emit_phase_event(5.5, "비타겟 LLM 보강", "started", 0, {"canPause": True})
    
    try:
        new_edges: list[GraphEdge] = []
        summaries_updated = 0
        
        # Build helper indexes
        log_process("ANALYZE", "PHASE_5.5", "헬퍼 인덱스 구축 중...")
        node_by_id: dict[str, GraphNode] = {n.node_id: n for n in nodes}
        chunk_by_id: dict[str, Chunk] = {}
        for c in chunks:
            chunk_by_id[c.chunk_id] = c
            for sc in c.sub_chunks:
                chunk_by_id[sc.chunk_id] = sc
        
        # Method→parent class mapping
        method_to_class: dict[str, str] = {}
        class_methods: dict[str, list[str]] = {}
        for edge in edges:
            if edge.edge_type == "HAS_METHOD":
                method_to_class[edge.target_id] = edge.source_id
                class_methods.setdefault(edge.source_id, []).append(edge.target_id)
        
        log_process("ANALYZE", "PHASE_5.5", f"인덱스 구축 완료: {len(node_by_id)}개 노드, {len(chunk_by_id)}개 청크")
        
        # Step A: SqlStatement summaries
        log_process("ANALYZE", "PHASE_5.5", "SQL Statement 요약 중...")
        sql_nodes = [n for n in nodes if n.label == "SqlStatement"]
        if sql_nodes:
            cnt = await _summarise_sql_statements(sql_nodes, client, llm_client)
            summaries_updated += cnt
            log_process("ANALYZE", "PHASE_5.5", f"{cnt}개 SQL Statement 요약 완료")
            yield emit_message(f"   ✓ SQL Statement {cnt}개 요약 완료")
        
        # Step B+C: Method summaries + CALLS_SQL + CALLS edges
        log_process("ANALYZE", "PHASE_5.5", "Method 요약 및 관계 발견 중...")
        method_nodes = [n for n in nodes if n.label == "METHOD"]
        if method_nodes:
            m_cnt, m_edges = await _summarise_methods(
                method_nodes, node_by_id, chunk_by_id,
                sql_index or {}, class_index or {}, method_to_class,
                client, llm_client,
            )
            summaries_updated += m_cnt
            new_edges.extend(m_edges)
            log_process("ANALYZE", "PHASE_5.5", f"{m_cnt}개 Method 요약, {len(m_edges)}개 새 관계 발견")
            yield emit_message(f"   ✓ Method {m_cnt}개 요약, {len(m_edges)}개 관계 발견")
        
        # Step D: Class summaries
        log_process("ANALYZE", "PHASE_5.5", "Class/Interface 요약 중...")
        class_nodes = [n for n in nodes if n.label in ("CLASS", "INTERFACE")]
        if class_nodes:
            c_cnt = await _summarise_classes(class_nodes, node_by_id, class_methods, client, llm_client)
            summaries_updated += c_cnt
            log_process("ANALYZE", "PHASE_5.5", f"{c_cnt}개 Class/Interface 요약 완료")
            yield emit_message(f"   ✓ Class/Interface {c_cnt}개 요약 완료")
        
        # Step E: Persist new edges to Neo4j
        if new_edges:
            log_process("ANALYZE", "PHASE_5.5", f"새 관계 {len(new_edges)}개 Neo4j에 저장 중...")
            batch_size = 30
            for i in range(0, len(new_edges), batch_size):
                batch = new_edges[i:i + batch_size]
                queries = _edges_to_cypher_queries(batch)
                async for batch_result in client.run_graph_query(queries):
                    yield emit_data(
                        graph={
                            "Nodes": batch_result.get("Nodes", []),
                            "Relationships": batch_result.get("Relationships", []),
                        }
                    )
            yield emit_message(f"   ✓ 새 관계 {len(new_edges)}개 저장 완료")
        
        log_process("ANALYZE", "PHASE_5.5", f"요약 {summaries_updated}개 업데이트, 새 관계 {len(new_edges)}개 생성")
        
        # Phase 5.5 완료 이벤트
        yield emit_phase_event(5.5, "비타겟 LLM 보강", "completed", 100, {
            "summaries_updated": summaries_updated,
            "new_edges": len(new_edges),
        })
        
        # Phase 결과 반환 (업데이트된 노드와 새로 생성된 엣지)
        yield emit_phase_result(5.5, {
            "updated_nodes": nodes,  # summary가 업데이트된 노드들
            "new_edges": new_edges,
        })
        
    except Exception as e:
        log_process("ANALYZE", "ERROR", f"Phase 5.5 실패: {e}", exc_info=True)
        yield emit_phase_event(5.5, "비타겟 LLM 보강", "failed", 0, {"error": str(e)})
        raise


async def _summarise_sql_statements(
    sql_nodes: list[GraphNode],
    client: Neo4jClient,
    llm_client: CustomLLMClient,
) -> int:
    """Summarise SQL statements using LLM."""
    # TODO: LLM을 사용한 SQL 요약 구현
    # 현재는 스킵 (나중에 구현)
    return 0


async def _summarise_methods(
    method_nodes: list[GraphNode],
    node_by_id: dict[str, GraphNode],
    chunk_by_id: dict[str, Chunk],
    sql_index: dict[str, Any],
    class_index: dict[str, Any],
    method_to_class: dict[str, str],
    client: Neo4jClient,
    llm_client: CustomLLMClient,
) -> tuple[int, list[GraphEdge]]:
    """Summarise methods and discover CALLS_SQL and CALLS edges."""
    new_edges: list[GraphEdge] = []
    summaries_updated = 0
    
    # TODO: LLM을 사용한 Method 요약 및 관계 발견 구현
    # 현재는 기본 로직만 구현
    
    for method_node in method_nodes:
        chunk = chunk_by_id.get(method_node.node_id)
        if not chunk:
            continue
        
        # Discover CALLS_SQL edges
        content = chunk.content
        if content:
            sql_calls = _SQL_CALL_PATTERN.findall(content)
            for sql_id in sql_calls:
                if sql_id in sql_index:
                    new_edges.append(GraphEdge(
                        source_id=method_node.node_id,
                        target_id=sql_index[sql_id]["chunk_id"],
                        edge_type="CALLS_SQL",  # Framework 스키마
                        properties={"discovery_method": "regex:sql_call"},
                    ))
        
        summaries_updated += 1
    
    return summaries_updated, new_edges


async def _summarise_classes(
    class_nodes: list[GraphNode],
    node_by_id: dict[str, GraphNode],
    class_methods: dict[str, list[str]],
    client: Neo4jClient,
    llm_client: CustomLLMClient,
) -> int:
    """Summarise classes/interfaces from child method summaries."""
    # TODO: LLM을 사용한 Class 요약 구현
    # 현재는 스킵 (나중에 구현)
    return 0


def _edges_to_cypher_queries(edges: list[GraphEdge]) -> list[dict[str, Any]]:
    """Convert GraphEdge list to Cypher queries."""
    queries: list[dict[str, Any]] = []
    
    for edge in edges:
        edge_type = edge.edge_type  # 이미 Framework 스키마에 맞춘 문자열
        props = edge.properties.copy()
        
        query = (
            "MATCH (__cy_src__) WHERE id(__cy_src__) = $source_id OR elementId(__cy_src__) = $source_id\n"
            "MATCH (__cy_tgt__) WHERE id(__cy_tgt__) = $target_id OR elementId(__cy_tgt__) = $target_id\n"
            f"MERGE (__cy_src__)-[__cy_r__:{edge_type}]->(__cy_tgt__)\n"
            "SET __cy_r__ += $props\n"
            "RETURN __cy_r__"
        )
        
        queries.append({
            "query": query,
            "params": {
                "source_id": edge.source_id,
                "target_id": edge.target_id,
                "props": props,
            },
        })
    
    return queries

