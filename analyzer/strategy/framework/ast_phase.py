"""AST 그래프 생성 Phase (Phase 1) - Framework

framework_analyzer.py에서 분리된 Phase 1 로직입니다.
모든 로직은 100% 보존되며, 위치만 변경되었습니다.
"""

import asyncio
import logging
from typing import Any, AsyncGenerator, List

from client.neo4j_client import Neo4jClient
from analyzer.strategy.base_analyzer import AnalysisStats
from analyzer.strategy.base.file_context import FileStatus, FileAnalysisContext
from analyzer.strategy.framework.ast_processor import FrameworkAstProcessor
from util.stream_event import (
    emit_data,
    emit_message,
    emit_phase_event,
    format_graph_result,
)
from util.text_utils import log_process


async def run_phase1(
    analyzer: Any,
    contexts: List[FileAnalysisContext],
    client: Neo4jClient,
    orchestrator: Any,
    stats: AnalysisStats,
) -> AsyncGenerator[bytes, None]:
    """Phase 1: 모든 파일의 AST 그래프를 병렬로 생성합니다.
    
    파일별 상태 기록:
    - 성공: PH1_OK → Phase 2 진행
    - 실패: PH1_FAIL → Phase 2 스킵 (토큰 절감)
    
    Args:
        analyzer: FrameworkAnalyzer 인스턴스 (공유 상태 접근용)
        contexts: 파일 컨텍스트 리스트
        client: Neo4j 클라이언트
        orchestrator: 오케스트레이터
        stats: 분석 통계
    """
    completed = 0
    total = len(contexts)
    results_queue: asyncio.Queue = asyncio.Queue()

    async def process_file(ctx: FileAnalysisContext):
        async with analyzer._file_semaphore:
            try:
                # AST JSON에서 last_line 추출 (children의 최대 endLine)
                last_line = 0
                for child in ctx.ast_data.get('children', []):
                    end = child.get('endLine', 0)
                    if end > last_line:
                        last_line = end
                
                processor = FrameworkAstProcessor(
                    antlr_data=ctx.ast_data,
                    directory=ctx.directory,
                    file_name=ctx.file_name,
                    api_key=orchestrator.api_key,
                    locale=orchestrator.locale,
                    last_line=last_line,
                )
                ctx.processor = processor
                
                # 정적 그래프 생성
                queries = processor.build_static_graph_queries()
                
                if queries:
                    all_nodes = {}
                    all_relationships = {}
                    async with analyzer._cypher_lock:
                        async for batch_result in client.run_graph_query(queries):
                            for node in batch_result.get("Nodes", []):
                                all_nodes[node["Node ID"]] = node
                            for rel in batch_result.get("Relationships", []):
                                all_relationships[rel["Relationship ID"]] = rel
                    
                    graph = {"Nodes": list(all_nodes.values()), "Relationships": list(all_relationships.values())}
                    node_count = len(graph.get("Nodes", []))
                    rel_count = len(graph.get("Relationships", []))
                    
                    ctx.status = FileStatus.PH1_OK
                    await results_queue.put({
                        "type": "success",
                        "file": ctx.file_name,
                        "graph": graph,
                        "node_count": node_count,
                        "rel_count": rel_count,
                    })
                else:
                    ctx.status = FileStatus.PH1_OK
                    await results_queue.put({
                        "type": "success",
                        "file": ctx.file_name,
                        "graph": {"Nodes": [], "Relationships": []},
                        "node_count": 0,
                        "rel_count": 0,
                    })
                    
            except Exception as e:
                log_process("ANALYZE", "ERROR", f"Phase 1 오류 ({ctx.file_name}): {e}", logging.ERROR, e)
                ctx.status = FileStatus.PH1_FAIL
                ctx.error_message = str(e)[:100]
                await results_queue.put({
                    "type": "error",
                    "file": ctx.file_name,
                    "message": str(e),
                })
                raise  # 즉시 중단 - 부분 실패 허용 안함

    # 모든 파일 병렬 처리 시작
    tasks = [asyncio.create_task(process_file(ctx)) for ctx in contexts]
    
    # Phase 1 시작 이벤트
    yield emit_phase_event(1, "AST 구조 생성", "started", 0, {"canPause": True, "files": total})

    # 결과 수신 및 스트리밍
    while completed < total:
        result = await asyncio.wait_for(results_queue.get(), timeout=300.0)
        result_type = result.get("type", "")
        
        completed += 1
        stats.files_completed = completed
        
        if result_type == "error":
            yield emit_message(f"   ❌ [{completed}/{total}] {result['file']}: {result['message'][:50]}")
            stats.mark_file_failed(result['file'], "Phase1 실패")
        else:
            stats.add_graph_result(result["graph"], is_static=True)
            
            graph = result["graph"]
            graph_msg = format_graph_result(graph)
            
            yield emit_message(f"   ✓ [{completed}/{total}] {result['file']}")
            if graph_msg:
                for line in graph_msg.split("\n")[:3]:  # 최대 3줄
                    yield emit_message(f"      {line}")
            
            yield emit_data(
                graph=graph,
                line_number=0,
                analysis_progress=int(completed / total * 50),
                current_file=result["file"],
            )
            
            # 진행률 이벤트
            progress = int(completed / total * 100)
            yield emit_phase_event(1, "AST 구조 생성", "progress", progress, {"completed": completed, "total": total})

    # 모든 작업 완료 대기
    await asyncio.gather(*tasks, return_exceptions=True)
    
    # Phase 1 완료 이벤트
    yield emit_phase_event(1, "AST 구조 생성", "completed", 100, {"nodes": stats.static_nodes_created})


async def run_phase1_5_association(
    analyzer: Any,
    contexts: List[FileAnalysisContext],
    client: Neo4jClient,
    orchestrator: Any,
    stats: AnalysisStats,
) -> AsyncGenerator[bytes, None]:
    """Phase 1.5: UML ASSOCIATION 관계 생성 (클래스별 병렬 처리)
    
    필드 기반 ASSOCIATION 후보 관계를 LLM으로 분석하여 생성합니다.
    
    Args:
        analyzer: FrameworkAnalyzer 인스턴스
        contexts: Phase 1 성공 파일 컨텍스트 리스트
        client: Neo4j 클라이언트
        orchestrator: 오케스트레이터
        stats: 분석 통계
    """
    from analyzer.strategy.framework.ast_processor import CLASS_TYPES, FIELD_TYPES, analyze_association
    
    # Phase 1 성공 파일만 처리
    success_contexts = [ctx for ctx in contexts if ctx.status == FileStatus.PH1_OK and ctx.processor]
    
    if not success_contexts:
        yield emit_message("   ℹ️ Phase 1.5: 처리할 파일 없음")
        return
    
    completed = 0
    total = len(success_contexts)
    results_queue: asyncio.Queue = asyncio.Queue()
    
    async def process_class_associations(ctx: FileAnalysisContext):
        """파일 내 모든 클래스의 ASSOCIATION 관계 생성"""
        async with analyzer._file_semaphore:
            try:
                if not ctx.processor:
                    raise RuntimeError(f"Phase 1에서 프로세서 초기화 실패: {ctx.file_name}")
                
                all_queries = []
                all_nodes = {}
                all_relationships = {}
                
                # 파일 내 모든 클래스 처리
                for node in ctx.processor._nodes:
                    if node.node_type not in CLASS_TYPES:
                        continue
                    
                    # 필드 수집
                    fields = [
                        {
                            "name": child.name,
                            "field_type": child.field_type,
                            "modifiers": child.modifiers,
                            "annotations": child.annotations,
                            "comment": child.comment,
                            "code": child.code,
                        }
                        for child in node.children
                        if child.node_type in FIELD_TYPES and child.field_type
                    ]
                    
                    if not fields:
                        continue
                    
                    # LLM 호출
                    class_meta_parts = []
                    if node.comment:
                        class_meta_parts.append(node.comment)
                    if node.annotations:
                        class_meta_parts.append(node.annotations)
                    class_meta = '\n'.join(class_meta_parts) if class_meta_parts else ""
                    
                    result = await asyncio.to_thread(
                        analyze_association,
                        node.unit_name,
                        class_meta,
                        fields,
                        orchestrator.api_key,
                        orchestrator.locale,
                    )
                    validated = ctx.processor.validate_dict_result(result, "association")
                    
                    # ASSOCIATION 생성
                    for rel in validated.get("fieldRelations", []):
                        target_class = rel.get("targetClass")
                        if not target_class:
                            continue
                        
                        multiplicity = rel.get("multiplicity", "1")
                        field_name = rel.get("field", "")
                        
                        query = {
                            "query": (
                                f"MATCH (__cy_src__:{node.node_type}:INTERNAL {{package_name: $package_name, class_name: $class_name}})\n"
                                f"OPTIONAL MATCH (__cy_int__:INTERNAL) WHERE (__cy_int__:CLASS OR __cy_int__:INTERFACE)\n"
                                f"  AND toLower(__cy_int__.class_name) = toLower($target_class)\n"
                                f"WITH __cy_src__, __cy_int__\n"
                                f"CALL {{\n"
                                f"  WITH __cy_src__, __cy_int__\n"
                                f"  WITH __cy_src__, __cy_int__ WHERE __cy_int__ IS NOT NULL\n"
                                f"  MERGE (__cy_src__)-[:ASSOCIATION {{field: $field_name, multiplicity: $multiplicity}}]->(__cy_int__)\n"
                                f"  RETURN 1 AS done\n"
                                f"  UNION ALL\n"
                                f"  WITH __cy_src__, __cy_int__\n"
                                f"  WITH __cy_src__ WHERE __cy_int__ IS NULL\n"
                                f"  MERGE (__cy_ext__:CLASS:EXTERNAL {{class_name: $target_class}})\n"
                                f"  SET __cy_ext__.name = $target_class\n"
                                f"  MERGE (__cy_src__)-[:ASSOCIATION {{field: $field_name, multiplicity: $multiplicity}}]->(__cy_ext__)\n"
                                f"  RETURN 1 AS done\n"
                                f"}}\n"
                                f"RETURN done"
                            ),
                            "params": {
                                "package_name": node.package_name,
                                "class_name": node.unit_name,
                                "target_class": target_class,
                                "field_name": field_name,
                                "multiplicity": multiplicity,
                            }
                        }
                        all_queries.append(query)
                        log_process("ANALYZE", "ASSOCIATION",
                                   f"   🔗 ASSOCIATION: {node.unit_name}.{field_name} → {target_class} ({multiplicity})")
                
                # 쿼리 실행 및 스트리밍
                if all_queries:
                    async with analyzer._cypher_lock:
                        async for batch_result in client.run_graph_query(all_queries):
                            for node in batch_result.get("Nodes", []):
                                all_nodes[node["Node ID"]] = node
                            for rel in batch_result.get("Relationships", []):
                                all_relationships[rel["Relationship ID"]] = rel
                            yield emit_data(
                                graph={
                                    "Nodes": batch_result.get("Nodes", []),
                                    "Relationships": batch_result.get("Relationships", []),
                                }
                            )
                    
                    log_process("ANALYZE", "PHASE_1.5", f"✅ {ctx.file_name}: ASSOCIATION {len(all_queries)}개 생성")
                
                await results_queue.put({
                    "type": "success",
                    "file": ctx.file_name,
                    "query_count": len(all_queries),
                })
                
            except Exception as e:
                log_process("ANALYZE", "ERROR", f"Phase 1.5 오류 ({ctx.file_name}): {e}", logging.ERROR, e)
                await results_queue.put({
                    "type": "error",
                    "file": ctx.file_name,
                    "message": str(e),
                })
                raise  # 즉시 중단 - 부분 실패 허용 안함
    
    # 모든 파일 병렬 처리 시작
    tasks = [asyncio.create_task(process_class_associations(ctx)) for ctx in success_contexts]
    
    # Phase 1.5 시작 이벤트
    yield emit_phase_event(1.5, "UML ASSOCIATION 생성", "started", 0, {"canPause": True, "files": total})
    
    # 결과 수신 및 스트리밍
    while completed < total:
        result = await asyncio.wait_for(results_queue.get(), timeout=300.0)
        result_type = result.get("type", "")
        
        completed += 1
        
        if result_type == "error":
            yield emit_message(f"   ❌ [{completed}/{total}] {result['file']}: {result['message'][:50]}")
        else:
            yield emit_message(f"   ✓ [{completed}/{total}] {result['file']}: {result.get('query_count', 0)}개 ASSOCIATION")
            
            # 진행률 이벤트
            progress = int(completed / total * 100)
            yield emit_phase_event(1.5, "UML ASSOCIATION 생성", "progress", progress, {"completed": completed, "total": total})
    
    # 모든 작업 완료 대기
    await asyncio.gather(*tasks, return_exceptions=True)
    
    # Phase 1.5 완료 이벤트
    yield emit_phase_event(1.5, "UML ASSOCIATION 생성", "completed", 100, {})
