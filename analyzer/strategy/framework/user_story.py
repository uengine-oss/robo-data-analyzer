"""User Story 생성 - Framework

llm_phase.py에서 분리된 Phase 2.3 로직입니다.
모든 파일의 모든 클래스를 병렬로 처리하여 User Story를 생성하고 즉시 저장합니다.
품질 필터링: 최대 5개, 중복 제거, 추상적인 것 제거
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


def filter_user_stories(user_stories: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """User Story 품질 필터링"""
    if not user_stories:
        return []
    
    # 1. 중복 제거 (goal 기준)
    seen_goals = set()
    unique_stories = []
    for us in user_stories:
        goal = us.get('goal', '').strip().lower()
        if goal and goal not in seen_goals:
            seen_goals.add(goal)
            unique_stories.append(us)
    
    # 2. 너무 추상적인 것 제거 (goal이 너무 짧거나 일반적인 단어만 포함)
    abstract_keywords = ['한다', '사용', '관리', '처리', '작업']  # 너무 일반적인 단어
    filtered_stories = []
    for us in unique_stories:
        goal = us.get('goal', '').strip()
        if len(goal) < 10:  # 너무 짧은 goal
            continue
        if any(keyword in goal for keyword in abstract_keywords) and len(goal.split()) < 5:
            continue  # 일반적인 단어만 있고 구체적이지 않음
        filtered_stories.append(us)
    
    # 3. 최대 5개로 제한
    return filtered_stories[:5]


async def run_phase2_3_user_story(
    analyzer: Any,
    contexts: List[FileAnalysisContext],
    client: Neo4jClient,
    orchestrator: Any,
    stats: AnalysisStats,
) -> AsyncGenerator[bytes, None]:
    """Phase 2.3: User Story 생성 (병렬 처리, 품질 필터링)
    
    모든 파일의 모든 클래스를 병렬로 처리하여 User Story를 생성하고 즉시 저장합니다.
    품질 필터링: 최대 5개, 중복 제거, 추상적인 것 제거
    """
    from analyzer.strategy.framework.ast_processor import analyze_class_user_story
    
    # Phase 2.1에서 생성한 클래스 summary가 있는 클래스만 처리
    all_classes = []
    for ctx in contexts:
        if (ctx.status == FileStatus.PH2_OK 
            and ctx.processor 
            and hasattr(ctx, 'class_summaries') 
            and ctx.class_summaries):
            for class_key, summary_data in ctx.class_summaries.items():
                info = ctx.processor._unit_info.get(class_key)
                if info:
                    all_classes.append((ctx, ctx.processor, class_key, info, summary_data))
    
    if not all_classes:
        yield emit_message("   ℹ️ Phase 2.3: 처리할 클래스 없음")
        return
    
    total = len(all_classes)
    completed = 0
    results_queue: asyncio.Queue = asyncio.Queue()
    semaphore = asyncio.Semaphore(settings.concurrency.max_concurrency)
    
    async def process_class(ctx: FileAnalysisContext, processor, class_key: str, info, summary_data: dict):
        """단일 클래스의 User Story 생성 및 저장"""
        async with semaphore:
            try:
                final_summary = summary_data["final_summary"]
                
                # Phase 2.3: User Story 생성
                if not final_summary:
                    user_stories_raw = []
                else:
                    us_result = await asyncio.to_thread(
                        analyze_class_user_story, final_summary, orchestrator.api_key, orchestrator.locale
                    )
                    validated = processor.validate_dict_result(us_result, "User Story")
                    user_stories_raw = validated.get('user_stories', [])
                    
                    if user_stories_raw:
                        log_process("ANALYZE", "PHASE_2.3", f"✅ {info.name}: User Story {len(user_stories_raw)}개 생성")
                
                # 품질 필터링
                user_stories = filter_user_stories(user_stories_raw)
                
                queries = []
                
                # User Story 저장
                for us_idx, us in enumerate(user_stories, 1):
                    us_id = us.get('id', f"US-{us_idx}")
                    
                    queries.append({
                        "query": (
                            f"MATCH (__cy_c__:{info.kind} {{start_line: $start_line, directory: $directory, file_name: $file_name}})\n"
                            f"MERGE (__cy_us__:UserStory {{id: $us_id, class_name: $class_name, directory: $directory, file_name: $file_name}})\n"
                            f"SET __cy_us__.role = $role, __cy_us__.goal = $goal, __cy_us__.benefit = $benefit\n"
                            f"MERGE (__cy_c__)-[:HAS_USER_STORY]->(__cy_us__)\n"
                            f"RETURN __cy_us__"
                        ),
                        "params": {
                            "start_line": info.node_start,
                            "directory": processor.full_directory,
                            "file_name": processor.file_name,
                            "us_id": us_id,
                            "class_name": info.name,
                            "role": us.get('role', ''),
                            "goal": us.get('goal', ''),
                            "benefit": us.get('benefit', ''),
                        }
                    })
                    
                    # Acceptance Criteria 저장
                    for ac_idx, ac in enumerate(us.get('acceptance_criteria', []), 1):
                        ac_id = ac.get('id', f"AC-{us_idx}-{ac_idx}")
                        
                        queries.append({
                            "query": (
                                f"MATCH (__cy_us__:UserStory {{id: $us_id, class_name: $class_name, directory: $directory, file_name: $file_name}})\n"
                                f"MERGE (__cy_ac__:AcceptanceCriteria {{id: $ac_id, user_story_id: $us_id, directory: $directory, file_name: $file_name}})\n"
                                f"SET __cy_ac__.title = $ac_title, __cy_ac__.given = $ac_given, __cy_ac__.when_clause = $ac_when, __cy_ac__.then_clause = $ac_then\n"
                                f"MERGE (__cy_us__)-[:HAS_AC]->(__cy_ac__)\n"
                                f"RETURN __cy_ac__"
                            ),
                            "params": {
                                "us_id": us_id,
                                "class_name": info.name,
                                "directory": processor.full_directory,
                                "file_name": processor.file_name,
                                "ac_id": ac_id,
                                "ac_title": ac.get('title', ''),
                                "ac_given": str(ac.get('given', [])),
                                "ac_when": str(ac.get('when', [])),
                                "ac_then": str(ac.get('then', [])),
                            }
                        })
                
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
                
                filtered_count = len(user_stories)
                original_count = len(user_stories_raw)
                if original_count > filtered_count:
                    log_process("ANALYZE", "PHASE_2.3", 
                               f"✅ {info.name}: User Story {original_count}개 → {filtered_count}개 (필터링)")
                else:
                    log_process("ANALYZE", "PHASE_2.3", 
                               f"✅ {info.name}: User Story {filtered_count}개 생성")
                
                await results_queue.put({
                    "type": "success",
                    "class": info.name,
                    "file": ctx.file_name,
                    "queries": queries,
                    "user_stories": filtered_count,
                })
                
            except Exception as e:
                log_process("ANALYZE", "ERROR", f"Phase 2.3 오류 ({ctx.file_name}/{info.name}): {e}", logging.ERROR, e)
                await results_queue.put({
                    "type": "error",
                    "class": info.name,
                    "file": ctx.file_name,
                    "message": str(e),
                })
                raise
    
    # Phase 2.3 시작 이벤트
    yield emit_phase_event(2.3, "User Story 생성", "started", 0, {"canPause": True, "classes": total})
    
    # 모든 클래스 병렬 처리
    tasks = [asyncio.create_task(process_class(ctx, proc, ck, inf, sd)) 
             for ctx, proc, ck, inf, sd in all_classes]
    
    # 결과 수신 및 스트리밍
    while completed < total:
        result = await asyncio.wait_for(results_queue.get(), timeout=300.0)
        result_type = result.get("type", "")
        
        completed += 1
        
        if result_type == "error":
            yield emit_message(f"   ❌ [{completed}/{total}] {result['file']}/{result['class']}: {result['message'][:50]}")
        else:
            us_count = result.get('user_stories', 0)
            yield emit_message(f"   ✓ [{completed}/{total}] {result['file']}/{result['class']}: User Story {us_count}개")
            
            progress = int(completed / total * 100)
            yield emit_phase_event(2.3, "User Story 생성", "progress", progress, {"completed": completed, "total": total})
    
    await asyncio.gather(*tasks, return_exceptions=True)
    
    yield emit_phase_event(2.3, "User Story 생성", "completed", 100, {"classes": total})

