"""User Story 생성 - DBMS

llm_phase.py에서 분리된 Phase 2.2 로직입니다.
모든 파일의 모든 프로시저를 병렬로 처리하여 User Story를 생성하고 즉시 저장합니다.
"""

import asyncio
import logging
from typing import Any, AsyncGenerator, List, Dict

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


def build_user_story_queries(
    procedure_type: str,
    procedure_name: str,
    directory: str,
    file_name: str,
    user_stories: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """User Story 및 Acceptance Criteria 쿼리 생성"""
    queries: List[Dict[str, Any]] = []
    
    for us_idx, us in enumerate(user_stories, 1):
        us_id = us.get('id', f"US-{us_idx}")
        queries.append({
            "query": (
                f"MATCH (__cy_p__:{procedure_type} {{procedure_name: $procedure_name, directory: $directory, file_name: $file_name}})\n"
                f"MERGE (__cy_us__:UserStory {{id: $us_id, procedure_name: $procedure_name, directory: $directory, file_name: $file_name}})\n"
                f"SET __cy_us__.role = $role, __cy_us__.goal = $goal, __cy_us__.benefit = $benefit\n"
                f"MERGE (__cy_p__)-[__cy_r__:HAS_USER_STORY]->(__cy_us__)\n"
                f"RETURN __cy_p__, __cy_us__, __cy_r__"
            ),
            "params": {
                "procedure_name": procedure_name,
                "directory": directory,
                "file_name": file_name,
                "us_id": us_id,
                "role": us.get('role', ''),
                "goal": us.get('goal', ''),
                "benefit": us.get('benefit', ''),
            }
        })
        
        for ac_idx, ac in enumerate(us.get('acceptance_criteria', []), 1):
            if not isinstance(ac, dict):
                continue
            ac_id = ac.get('id', f"AC-{us_idx}-{ac_idx}")
            queries.append({
                "query": (
                    f"MATCH (__cy_us__:UserStory {{id: $us_id, directory: $directory, file_name: $file_name}})\n"
                    f"MERGE (__cy_ac__:AcceptanceCriteria {{id: $ac_id, user_story_id: $us_id, directory: $directory, file_name: $file_name}})\n"
                    f"SET __cy_ac__.title = $title, __cy_ac__.given = $given, __cy_ac__.when_clause = $when_clause, __cy_ac__.then_clause = $then_clause\n"
                    f"MERGE (__cy_us__)-[__cy_r__:HAS_AC]->(__cy_ac__)\n"
                    f"RETURN __cy_us__, __cy_ac__, __cy_r__"
                ),
                "params": {
                    "us_id": us_id,
                    "ac_id": ac_id,
                    "directory": directory,
                    "file_name": file_name,
                    "title": ac.get('title', ''),
                    "given": str(ac.get('given', [])),
                    "when_clause": str(ac.get('when', [])),
                    "then_clause": str(ac.get('then', [])),
                }
            })
    
    return queries


async def run_phase2_2_user_story(
    analyzer: Any,
    contexts: List[FileAnalysisContext],
    client: Neo4jClient,
    orchestrator: Any,
    stats: AnalysisStats,
) -> AsyncGenerator[bytes, None]:
    """Phase 2.2: User Story 생성 (병렬 처리)
    
    모든 파일의 모든 프로시저를 병렬로 처리하여 User Story를 생성하고 즉시 저장합니다.
    """
    from analyzer.strategy.dbms.ast_processor import analyze_user_story
    
    # Phase 2.1에서 생성한 프로시저 summary가 있는 프로시저만 처리
    all_procedures = []
    for ctx in contexts:
        if (ctx.status == FileStatus.PH2_OK 
            and ctx.processor 
            and hasattr(ctx, 'procedure_summaries') 
            and ctx.procedure_summaries):
            for proc_key, summary_data in ctx.procedure_summaries.items():
                info = summary_data.get("procedure_info")
                if info:
                    all_procedures.append((ctx, ctx.processor, proc_key, info, summary_data))
    
    if not all_procedures:
        yield emit_message("   ℹ️ Phase 2.2: 처리할 프로시저 없음")
        return
    
    total = len(all_procedures)
    completed = 0
    results_queue: asyncio.Queue = asyncio.Queue()
    semaphore = asyncio.Semaphore(settings.concurrency.max_concurrency)
    
    async def process_procedure(ctx: FileAnalysisContext, processor, proc_key: str, info, summary_data: dict):
        """단일 프로시저의 User Story 생성 및 저장"""
        async with semaphore:
            try:
                final_summary = summary_data["final_summary"]
                
                # Phase 2.2: User Story 생성
                if not final_summary:
                    user_stories_raw = []
                else:
                    us_result = await asyncio.to_thread(
                        analyze_user_story, final_summary, orchestrator.api_key, orchestrator.locale
                    )
                    # 결과 검증 (간단한 검증만 수행)
                    if not isinstance(us_result, dict):
                        user_stories_raw = []
                    else:
                        user_stories_raw = us_result.get('user_stories', []) or []
                    
                    if user_stories_raw:
                        log_process("ANALYZE", "PHASE_2.2", f"✅ {info.procedure_name}: User Story {len(user_stories_raw)}개 생성")
                
                queries = []
                
                # User Story 저장
                if user_stories_raw:
                    us_queries = build_user_story_queries(
                        info.procedure_type,
                        info.procedure_name,
                        processor.full_directory,
                        processor.file_name,
                        user_stories_raw,
                    )
                    queries.extend(us_queries)
                
                # 저장 및 스트리밍 (30개씩 배치)
                if queries:
                    batch_size = 30
                    for i in range(0, len(queries), batch_size):
                        batch = queries[i:i + batch_size]
                        async with analyzer._cypher_lock:
                            async for batch_result in client.run_graph_query(batch):
                                yield emit_data(
                                    graph={
                                        "Nodes": batch_result.get("Nodes", []),
                                        "Relationships": batch_result.get("Relationships", []),
                                    }
                                )
                
                await results_queue.put({
                    "type": "success",
                    "procedure": info.procedure_name,
                    "file": ctx.file_name,
                    "queries": queries,
                    "user_stories": len(user_stories_raw),
                })
                
            except Exception as e:
                log_process("ANALYZE", "ERROR", f"Phase 2.2 오류 ({ctx.file_name}/{info.procedure_name}): {e}", logging.ERROR, e)
                await results_queue.put({
                    "type": "error",
                    "procedure": info.procedure_name,
                    "file": ctx.file_name,
                    "message": str(e),
                })
                raise
    
    # Phase 2.2 시작 이벤트
    yield emit_phase_event(2.2, "User Story 생성", "started", 0, {"canPause": True, "procedures": total})
    
    # 모든 프로시저 병렬 처리
    tasks = [asyncio.create_task(process_procedure(ctx, proc, pk, inf, sd)) 
             for ctx, proc, pk, inf, sd in all_procedures]
    
    # 결과 수신 및 스트리밍
    while completed < total:
        result = await asyncio.wait_for(results_queue.get(), timeout=300.0)
        result_type = result.get("type", "")
        
        completed += 1
        
        if result_type == "error":
            yield emit_message(f"   ❌ [{completed}/{total}] {result['file']}/{result['procedure']}: {result['message'][:50]}")
        else:
            us_count = result.get('user_stories', 0)
            yield emit_message(f"   ✓ [{completed}/{total}] {result['file']}/{result['procedure']}: User Story {us_count}개")
            
            progress = int(completed / total * 100)
            yield emit_phase_event(2.2, "User Story 생성", "progress", progress, {"completed": completed, "total": total})
    
    await asyncio.gather(*tasks, return_exceptions=True)
    
    yield emit_phase_event(2.2, "User Story 생성", "completed", 100, {"procedures": total})

