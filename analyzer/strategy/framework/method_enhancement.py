"""메서드 Summary 보강 - Framework

llm_phase.py에서 분리된 Phase 2.0 로직입니다.
전체 그래프를 기반으로 메서드 summary를 보강합니다.
"""

import asyncio
import logging
from typing import Any, AsyncGenerator, Dict, List, Optional, Tuple

from client.neo4j_client import Neo4jClient
from analyzer.strategy.base_analyzer import AnalysisStats
from analyzer.strategy.base.file_context import FileStatus, FileAnalysisContext
from config.settings import settings
from util.stream_event import (
    emit_data,
    emit_message,
    emit_phase_event,
)
from util.text_utils import log_process

from analyzer.strategy.framework.ast_processor import analyze_method_summary_enhancement
from analyzer.strategy.framework.ast_processor import CLASS_TYPES


async def fetch_call_graph_from_neo4j(client: Neo4jClient) -> Dict[str, Any]:
    """Neo4j에서 CALLS 관계 그래프를 한 번에 조회 (전체 그래프)"""
    query = """
    MATCH (p:METHOD)-[:CALLS]->(c:METHOD)
    WITH
      collect(DISTINCT {from: elementId(p), to: elementId(c)}) AS edges,
      collect(DISTINCT p) + collect(DISTINCT c) AS ns
    UNWIND ns AS n
    WITH
      edges,
      collect(DISTINCT { 
        id: elementId(n), 
        name: coalesce(n.name, toString(n.start_line)),
        start_line: n.start_line,
        directory: n.directory,
        file_name: n.file_name,
        summary: coalesce(n.summary, ''),
        class_name: n.class_name
      }) AS nodes
    RETURN
      nodes,
      edges,
      [x IN nodes WHERE NOT EXISTS {
        MATCH (m:METHOD) WHERE elementId(m) = x.id
        MATCH (m)-[:CALLS]->(:METHOD)
      } | x.id] AS leafIds
    """
    
    results = await client.execute_queries([{"query": query}])
    if not results or not results[0]:
        return {"nodes": [], "edges": [], "leafIds": []}
    
    result = results[0][0]
    return {
        "nodes": result.get("nodes", []),
        "edges": result.get("edges", []),
        "leafIds": result.get("leafIds", []),
    }


def build_call_graph_structure(
    nodes: List[Dict[str, Any]],
    edges: List[Dict[str, Any]],
    leafIds: List[str],
) -> Tuple[Dict[str, List[str]], Dict[str, List[str]], Dict[str, int], Dict[str, Dict[str, Any]]]:
    """그래프 구조 구축 (children, parents, remaining_children, node_map)
    
    ready_queue는 여기서 만들지 않음.
    leaf 노드 캐시 + 부모 remaining 차감 후에 결정되므로 호출측에서 처리.
    """
    # 노드 맵 생성
    node_map: Dict[str, Dict[str, Any]] = {node["id"]: node for node in nodes}
    
    # 1. children[from] = [to...]  (호출하는 메서드 → 호출되는 메서드들)
    children: Dict[str, List[str]] = {}
    for edge in edges:
        children.setdefault(edge["from"], []).append(edge["to"])
    
    # 2. parents[to] = [from...]   (호출되는 메서드 → 호출하는 메서드들)
    parents: Dict[str, List[str]] = {}
    for edge in edges:
        parents.setdefault(edge["to"], []).append(edge["from"])
    
    # 3. remaining_children[from] = len(children[from])
    # CALL 관계가 있는 노드만 remaining_children에 포함
    remaining_children: Dict[str, int] = {}
    for node_id, callees in children.items():
        remaining_children[node_id] = len(callees)
    
    return children, parents, remaining_children, node_map


def get_class_meta_for_method(
    contexts: List[FileAnalysisContext],
    class_name: Optional[str],
) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """메서드가 속한 클래스의 메타데이터 조회
    
    contexts를 순회하여 해당 class_name을 가진 processor를 찾고,
    processor._nodes에서 클래스 노드를 찾아 메타데이터를 반환합니다.
    """
    if not class_name:
        return None, None, None
    
    # contexts를 순회하여 해당 class_name을 가진 processor 찾기
    for ctx in contexts:
        if not ctx.processor:
            continue
        
        processor = ctx.processor
        # processor._nodes에서 클래스 노드 찾기
        class_node = next(
            (n for n in processor._nodes 
             if n.node_type in CLASS_TYPES 
             and n.unit_name == class_name),
            None,
        )
        
        if class_node:
            return class_name, class_node.comment, class_node.annotations
    
    # 찾지 못한 경우
    return class_name, None, None


async def enhance_method_summary(
    contexts: List[FileAnalysisContext],
    node: Dict[str, Any],
    called_summaries: List[Dict[str, Any]],
    api_key: str,
    locale: str,
) -> str:
    """개별 메서드의 summary를 보강"""
    current_summary = node.get("summary", "")
    if not current_summary:
        return ""
    
    class_name, class_comment, class_annotations = get_class_meta_for_method(
        contexts,
        node.get("class_name")
    )
    
    result = await asyncio.to_thread(
        analyze_method_summary_enhancement,
        current_summary,
        called_summaries,
        api_key,
        locale,
        class_name,
        class_comment,
        class_annotations,
    )
    
    # 결과 검증 (간단한 검증만 수행)
    if not isinstance(result, dict):
        return current_summary
    
    return result.get("summary", current_summary)


async def run_phase2_0_enhancement(
    analyzer: Any,
    contexts: List[FileAnalysisContext],
    client: Neo4jClient,
    orchestrator: Any,
    stats: AnalysisStats,
) -> AsyncGenerator[bytes, None]:
    """Phase 2.0: 메서드 Summary 보강 (전체 그래프, 모든 파일 완료 후)
    
    전체 그래프를 기반으로 메서드 summary를 보강합니다.
    """
    log_process("ANALYZE", "PHASE_2.0", f"🚀 Phase 2.0: 전체 그래프 메서드 summary 보강 시작")
    
    # Neo4j에서 그래프 조회
    graph_data = await fetch_call_graph_from_neo4j(client)
    nodes = graph_data.get("nodes", [])
    edges = graph_data.get("edges", [])
    leafIds = graph_data.get("leafIds", [])
    
    if not nodes:
        log_process("ANALYZE", "PHASE_2.0", f"⚠️ 전체 그래프: CALL 관계 없음")
        yield emit_phase_event(2.0, "메서드 Summary 보강", "completed", 100, {"queries": 0})
        return
    
    # 그래프 구조 구축
    children, parents, remaining_children, node_map = build_call_graph_structure(
        nodes, edges, leafIds
    )
    
    log_process("ANALYZE", "PHASE_2.0", f"📊 전체 그래프: 구조 구축 완료 (노드 {len(nodes)}개, 엣지 {len(edges)}개, leaf {len(leafIds)}개)")
    
    # summary_cache 초기화
    summary_cache: Dict[str, str] = {}
    ready_queue: List[str] = []
    
    # leaf 노드(outDegree=0 = 다른 메서드를 호출하지 않는 메서드)의 기존 summary를 캐시에 저장
    # 이들은 LLM 보강 대상이 아니며, 그 부모 노드가 ready가 될 수 있도록 remaining_children을 차감
    for leaf_id in leafIds:
        if leaf_id in node_map:
            summary_cache[leaf_id] = node_map[leaf_id].get("summary", "")
        
        # 이 leaf를 호출하는 부모들의 remaining_children 차감
        if leaf_id in parents:
            for parent_id in parents[leaf_id]:
                if parent_id in remaining_children:
                    remaining_children[parent_id] -= 1
                    if remaining_children[parent_id] == 0:
                        ready_queue.append(parent_id)
    
    if not ready_queue:
        log_process("ANALYZE", "PHASE_2.0", f"⚠️ 전체 그래프: 보강 대상 메서드 없음 (leaf만 존재)")
        yield emit_phase_event(2.0, "메서드 Summary 보강", "completed", 100, {"queries": 0})
        return
    
    log_process("ANALYZE", "PHASE_2.0", f"🚀 전체 그래프: leaf {len(leafIds)}개 캐시 완료, 초기 ready_queue {len(ready_queue)}개")
    
    # Ready Queue 기반 처리
    queries: List[Dict[str, Any]] = []
    step = 0
    # 동시 처리 수 제한 (환경변수에서 읽기)
    max_concurrent = settings.concurrency.max_concurrency
    semaphore = asyncio.Semaphore(max_concurrent)
    
    # 모든 contexts의 processor._unit_info를 수집 (unit_summary_store 업데이트용)
    unit_info_map: Dict[str, Dict[str, Any]] = {}
    for ctx in contexts:
        if ctx.processor and ctx.processor._unit_info:
            for class_key, info in ctx.processor._unit_info.items():
                unit_info_map[info.name] = {"class_key": class_key, "info": info}
    
    while ready_queue:
        step += 1
        # 현재 레벨의 노드들을 병렬로 처리
        current_batch = list(ready_queue)  # 복사본 생성 (참조 복사 방지)
        ready_queue = []
        
        log_process("ANALYZE", "PHASE_2.0", f"  Step {step}: ready_queue = {len(current_batch)}개 노드 처리 시작 (동시 처리: {max_concurrent}개)")
        
        # 병렬 처리 (제한 적용)
        tasks = []
        for node_id in current_batch:
            if node_id not in node_map:
                continue
            
            node = node_map[node_id]
            
            # 호출된 메서드들의 summary 수집
            called_summaries = []
            for callee_id in children.get(node_id, []):
                if callee_id in summary_cache:
                    callee_node = node_map.get(callee_id)
                    if callee_node:
                        called_summaries.append({
                            "class_name": callee_node.get("class_name", ""),
                            "method_name": callee_node.get("name", ""),
                            "summary": summary_cache[callee_id]
                        })
            
            # Semaphore로 동시 처리 수 제한
            async def process_with_limit(n, cs):
                async with semaphore:
                    return await enhance_method_summary(
                        contexts, n, cs, orchestrator.api_key, orchestrator.locale
                    )
            
            task = process_with_limit(node, called_summaries)
            tasks.append((node_id, task))
        
        # 병렬 실행
        if tasks:
            enhanced_summaries = await asyncio.gather(*[task for _, task in tasks])
            
            # 결과 저장 및 부모 노드 준비 상태 업데이트
            for (node_id, _), enhanced_summary in zip(tasks, enhanced_summaries):
                summary_cache[node_id] = enhanced_summary
                
                # Neo4j 업데이트 쿼리 생성 및 unit_summary_store 업데이트
                if node_id in node_map:
                    node = node_map[node_id]
                    queries.append({
                        "query": (
                            f"MATCH (__cy_m__:METHOD {{start_line: $start_line, directory: $directory, file_name: $file_name}})\n"
                            f"SET __cy_m__.summary = $summary\n"
                            f"RETURN __cy_m__"
                        ),
                        "params": {
                            "start_line": node.get("start_line"),
                            "directory": node.get("directory"),
                            "file_name": node.get("file_name"),
                            "summary": enhanced_summary,
                        }
                    })
                    
                    # unit_summary_store 업데이트
                    class_name = node.get("class_name")
                    if class_name and class_name in unit_info_map:
                        class_key = unit_info_map[class_name]["class_key"]
                        # contexts에서 해당 class_key를 가진 context 찾기
                        for ctx in contexts:
                            if (ctx.processor 
                                and hasattr(ctx, 'unit_summary_store') 
                                and ctx.unit_summary_store
                                and class_key in ctx.processor._unit_info):
                                key = f"METHOD_{node.get('start_line')}_{node.get('start_line')}"
                                if class_key not in ctx.unit_summary_store:
                                    ctx.unit_summary_store[class_key] = {}
                                ctx.unit_summary_store[class_key][key] = enhanced_summary
                                break
                
                # 부모 노드들의 remaining_children 감소
                if node_id in parents:
                    for parent_id in parents[node_id]:
                        if parent_id in remaining_children:
                            remaining_children[parent_id] -= 1
                            if remaining_children[parent_id] == 0:
                                ready_queue.append(parent_id)
            
            log_process("ANALYZE", "PHASE_2.0", f"  Step {step}: {len(tasks)}개 메서드 보강 완료")
    
    log_process("ANALYZE", "PHASE_2.0", f"✅ 전체 그래프: 메서드 summary 보강 완료 ({len(queries)}개 쿼리 생성)")
    
    # 쿼리 실행 및 스트리밍
    if queries:
        enhancement_nodes = {}
        enhancement_relationships = {}
        async with analyzer._cypher_lock:
            async for batch_result in client.run_graph_query(queries):
                for node in batch_result.get("Nodes", []):
                    enhancement_nodes[node["Node ID"]] = node
                for rel in batch_result.get("Relationships", []):
                    enhancement_relationships[rel["Relationship ID"]] = rel
                yield emit_data(
                    graph={
                        "Nodes": batch_result.get("Nodes", []),
                        "Relationships": batch_result.get("Relationships", []),
                    }
                )
        log_process("ANALYZE", "PHASE_2.0", f"✅ Phase 2.0: 전체 그래프 메서드 summary 보강 완료 ({len(queries)}개 쿼리, 노드 {len(enhancement_nodes)}개, 관계 {len(enhancement_relationships)}개)")
    else:
        log_process("ANALYZE", "PHASE_2.0", f"⚠️ Phase 2.0: 보강할 메서드 없음")
    
    yield emit_phase_event(2.0, "메서드 Summary 보강", "completed", 100, {"queries": len(queries)})
