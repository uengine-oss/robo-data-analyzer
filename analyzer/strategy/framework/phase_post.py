"""후처리 Phase (Phase 2.0~2.3) - Framework

메서드 Summary 보강, 클래스 Summary, 스테레오타입, User Story 통합.
"""

import asyncio
import json
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
from util.text_utils import calculate_code_token, log_process


def _split_summaries_by_token(summaries: dict, max_token: int) -> List[dict]:
    """토큰 기준으로 summaries를 청크로 분할합니다."""
    if not summaries:
        return []
    chunks = []
    current_chunk = {}
    current_tokens = 0
    for key, value in summaries.items():
        item_text = f"{key}: {value}"
        item_tokens = calculate_code_token(item_text)
        if current_tokens + item_tokens > max_token and current_chunk:
            chunks.append(current_chunk)
            current_chunk = {}
            current_tokens = 0
        current_chunk[key] = value
        current_tokens += item_tokens
    if current_chunk:
        chunks.append(current_chunk)
    return chunks


def _filter_user_stories(user_stories: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """User Story 품질 필터링"""
    if not user_stories:
        return []
    seen_goals = set()
    unique_stories = []
    for us in user_stories:
        goal = us.get('goal', '').strip().lower()
        if goal and goal not in seen_goals:
            seen_goals.add(goal)
            unique_stories.append(us)
    abstract_keywords = ['한다', '사용', '관리', '처리', '작업']
    filtered_stories = []
    for us in unique_stories:
        goal = us.get('goal', '').strip()
        if len(goal) < 10:
            continue
        if any(keyword in goal for keyword in abstract_keywords) and len(goal.split()) < 5:
            continue
        filtered_stories.append(us)
    return filtered_stories[:5]


async def _fetch_call_graph_from_neo4j(client: Neo4jClient) -> Dict[str, Any]:
    """Neo4j에서 CALLS 관계 그래프를 조회"""
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


def _build_call_graph_structure(
    nodes: List[Dict[str, Any]],
    edges: List[Dict[str, Any]],
    leafIds: List[str],
) -> Tuple[Dict[str, List[str]], Dict[str, List[str]], Dict[str, int], Dict[str, Dict[str, Any]]]:
    """그래프 구조 구축"""
    node_map: Dict[str, Dict[str, Any]] = {node["id"]: node for node in nodes}
    children: Dict[str, List[str]] = {}
    for edge in edges:
        children.setdefault(edge["from"], []).append(edge["to"])
    parents: Dict[str, List[str]] = {}
    for edge in edges:
        parents.setdefault(edge["to"], []).append(edge["from"])
    remaining_children: Dict[str, int] = {}
    for node_id, callees in children.items():
        remaining_children[node_id] = len(callees)
    return children, parents, remaining_children, node_map


def _get_class_meta_for_method(
    contexts: List[FileAnalysisContext],
    class_name: Optional[str],
    class_types: frozenset,
) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """메서드가 속한 클래스의 메타데이터 조회"""
    if not class_name:
        return None, None, None
    for ctx in contexts:
        if not ctx.processor:
            continue
        processor = ctx.processor
        class_node = next(
            (n for n in processor._nodes
             if n.node_type in class_types and n.unit_name == class_name),
            None,
        )
        if class_node:
            return class_name, class_node.comment, class_node.annotations
    return class_name, None, None


async def run_phase_post(
    pipeline: Any,
    contexts: List[FileAnalysisContext],
    client: Neo4jClient,
    orchestrator: Any,
    stats: AnalysisStats,
) -> AsyncGenerator[bytes, None]:
    """Phase 2.0~2.3: 메서드 보강, 클래스 Summary, 스테레오타입, User Story 순차 실행"""
    from analyzer.strategy.framework._ast_core import (
        analyze_method_summary_enhancement,
        analyze_stereotype,
        analyze_class_user_story,
        CLASS_TYPES,
        METHOD_TYPES,
    )

    MAX_SUMMARY_CHUNK_TOKEN = settings.batch.max_summary_chunk_token

    async def _run_enhancement():
        """Phase 2.0: 메서드 Summary 보강"""
        log_process("ANALYZE", "PHASE_2.0", "🚀 Phase 2.0: 전체 그래프 메서드 summary 보강 시작")
        graph_data = await _fetch_call_graph_from_neo4j(client)
        nodes = graph_data.get("nodes", [])
        edges = graph_data.get("edges", [])
        leafIds = graph_data.get("leafIds", [])

        if not nodes:
            log_process("ANALYZE", "PHASE_2.0", "⚠️ 전체 그래프: CALL 관계 없음")
            yield emit_phase_event(2.0, "메서드 Summary 보강", "completed", 100, {"queries": 0})
            return

        children, parents, remaining_children, node_map = _build_call_graph_structure(nodes, edges, leafIds)
        log_process("ANALYZE", "PHASE_2.0", f"📊 전체 그래프: 노드 {len(nodes)}개, 엣지 {len(edges)}개, leaf {len(leafIds)}개")

        summary_cache: Dict[str, str] = {}
        ready_queue: List[str] = []
        for leaf_id in leafIds:
            if leaf_id in node_map:
                summary_cache[leaf_id] = node_map[leaf_id].get("summary", "")
            if leaf_id in parents:
                for parent_id in parents[leaf_id]:
                    if parent_id in remaining_children:
                        remaining_children[parent_id] -= 1
                        if remaining_children[parent_id] == 0:
                            ready_queue.append(parent_id)

        if not ready_queue:
            log_process("ANALYZE", "PHASE_2.0", "⚠️ 전체 그래프: 보강 대상 메서드 없음")
            yield emit_phase_event(2.0, "메서드 Summary 보강", "completed", 100, {"queries": 0})
            return

        max_concurrent = settings.concurrency.max_concurrency
        semaphore = asyncio.Semaphore(max_concurrent)
        unit_info_map: Dict[str, Dict[str, Any]] = {}
        for ctx in contexts:
            if ctx.processor and ctx.processor._unit_info:
                for class_key, info in ctx.processor._unit_info.items():
                    unit_info_map[info.name] = {"class_key": class_key, "info": info}

        queries: List[Dict[str, Any]] = []
        step = 0

        async def enhance_method_summary(node: Dict, called_summaries: List[Dict]) -> str:
            current_summary = node.get("summary", "")
            if not current_summary:
                return ""
            class_name, class_comment, class_annotations = _get_class_meta_for_method(
                contexts, node.get("class_name"), CLASS_TYPES
            )
            result = await asyncio.to_thread(
                analyze_method_summary_enhancement,
                current_summary,
                called_summaries,
                orchestrator.api_key,
                orchestrator.locale,
                class_name,
                class_comment,
                class_annotations,
            )
            if not isinstance(result, dict):
                return current_summary
            return result.get("summary", current_summary)

        while ready_queue:
            step += 1
            current_batch = list(ready_queue)
            ready_queue = []
            log_process("ANALYZE", "PHASE_2.0", f"  Step {step}: {len(current_batch)}개 노드 처리")

            tasks = []
            for node_id in current_batch:
                if node_id not in node_map:
                    continue
                node = node_map[node_id]
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

                async def process_with_limit(n, cs):
                    async with semaphore:
                        return await enhance_method_summary(n, cs)

                tasks.append((node_id, process_with_limit(node, called_summaries)))

            if tasks:
                enhanced_summaries = await asyncio.gather(*[t[1] for t in tasks])
                for (node_id, _), enhanced_summary in zip(tasks, enhanced_summaries):
                    summary_cache[node_id] = enhanced_summary
                    if node_id in node_map:
                        node = node_map[node_id]
                        queries.append({
                            "query": (
                                "MATCH (__cy_m__:METHOD {start_line: $start_line, directory: $directory, file_name: $file_name})\n"
                                "SET __cy_m__.summary = $summary\n"
                                "RETURN __cy_m__"
                            ),
                            "params": {
                                "start_line": node.get("start_line"),
                                "directory": node.get("directory"),
                                "file_name": node.get("file_name"),
                                "summary": enhanced_summary,
                            }
                        })
                        class_name = node.get("class_name")
                        if class_name and class_name in unit_info_map:
                            for ctx in contexts:
                                if (ctx.processor and hasattr(ctx, 'unit_summary_store') and ctx.unit_summary_store
                                    and class_name in {ctx.processor._unit_info[k].name for k in ctx.processor._unit_info}):
                                    class_key = next(k for k, v in ctx.processor._unit_info.items() if v.name == class_name)
                                    if class_key not in ctx.unit_summary_store:
                                        ctx.unit_summary_store[class_key] = {}
                                    key = f"METHOD_{node.get('start_line')}_{node.get('start_line')}"
                                    ctx.unit_summary_store[class_key][key] = enhanced_summary
                                    break
                    if node_id in parents:
                        for parent_id in parents[node_id]:
                            if parent_id in remaining_children:
                                remaining_children[parent_id] -= 1
                                if remaining_children[parent_id] == 0:
                                    ready_queue.append(parent_id)

        if queries:
            async with pipeline._cypher_lock:
                async for batch_result in client.run_graph_query(queries):
                    yield emit_data(graph={
                        "Nodes": batch_result.get("Nodes", []),
                        "Relationships": batch_result.get("Relationships", []),
                    })
        yield emit_phase_event(2.0, "메서드 Summary 보강", "completed", 100, {"queries": len(queries)})

    async def _run_class_summary():
        """Phase 2.1: 클래스 Summary 생성"""
        success_contexts = [ctx for ctx in contexts
                           if ctx.status == FileStatus.PH2_OK and ctx.processor
                           and hasattr(ctx, 'unit_summary_store') and ctx.unit_summary_store]
        if not success_contexts:
            yield emit_message("   ℹ️ Phase 2.1: 처리할 파일 없음")
            return

        all_classes = []
        for ctx in success_contexts:
            processor = ctx.processor
            for class_key, info in processor._unit_info.items():
                summaries = ctx.unit_summary_store.get(class_key, {})
                if summaries:
                    class_node = next(
                        (n for n in processor._nodes
                         if n.start_line == info.node_start and n.node_type == info.kind),
                        None,
                    )
                    if class_node and class_node.ok:
                        all_classes.append((ctx, processor, class_key, info, summaries, class_node))

        if not all_classes:
            yield emit_message("   ℹ️ Phase 2.1: 처리할 클래스 없음")
            return

        total = len(all_classes)
        completed = 0
        results_queue: asyncio.Queue = asyncio.Queue()
        semaphore = asyncio.Semaphore(settings.concurrency.max_concurrency)

        async def process_class(ctx, processor, class_key, info, summaries, class_node):
            async with semaphore:
                try:
                    chunks = _split_summaries_by_token(summaries, MAX_SUMMARY_CHUNK_TOKEN)
                    if not chunks:
                        final_summary = ""
                    else:
                        class_meta_parts = []
                        if class_node.comment:
                            class_meta_parts.append(class_node.comment)
                        if class_node.annotations:
                            class_meta_parts.append(class_node.annotations)
                        class_meta = '\n'.join(class_meta_parts) if class_meta_parts else ""
                        chunk_semaphore = asyncio.Semaphore(settings.concurrency.max_concurrency)

                        async def process_chunk(idx: int, chunk: dict):
                            async with chunk_semaphore:
                                result = await asyncio.to_thread(
                                    analyze_stereotype, chunk, orchestrator.api_key,
                                    orchestrator.locale, class_meta
                                )
                                return processor.validate_dict_result(result, "청크 분석")

                        chunk_results = [r for r in await asyncio.gather(
                            *[process_chunk(idx, c) for idx, c in enumerate(chunks)]
                        ) if r]
                        if not chunk_results:
                            raise RuntimeError(f"{info.name}: 청크 처리 결과 없음")
                        if len(chunk_results) == 1:
                            final_summary = chunk_results[0].get('summary', '')
                        else:
                            combined = {f"CHUNK_{i+1}": r.get('summary', '') for i, r in enumerate(chunk_results)}
                            result = await asyncio.to_thread(
                                analyze_stereotype, combined, orchestrator.api_key,
                                orchestrator.locale, class_meta
                            )
                            validated = processor.validate_dict_result(result, "청크 통합")
                            final_summary = validated.get('summary', '')
                        log_process("ANALYZE", "PHASE_2.1", f"✅ {info.name}: 클래스 summary 생성 완료")

                    if not final_summary:
                        await results_queue.put({"type": "success", "class": info.name, "file": ctx.file_name})
                        return

                    queries = [{
                        "query": (
                            f"MATCH (__cy_n__:{info.kind} {{start_line: $start_line, directory: $directory, file_name: $file_name}})\n"
                            "SET __cy_n__.summary = $summary\n"
                            "RETURN __cy_n__"
                        ),
                        "params": {
                            "start_line": info.node_start,
                            "directory": processor.full_directory,
                            "file_name": processor.file_name,
                            "summary": str(final_summary),
                        }
                    }]
                    if queries:
                        async with pipeline._cypher_lock:
                            async for batch_result in client.run_graph_query(queries):
                                pass
                    if not hasattr(ctx, 'class_summaries'):
                        ctx.class_summaries = {}
                    ctx.class_summaries[class_key] = {"final_summary": final_summary, "class_node": class_node}
                    await results_queue.put({"type": "success", "class": info.name, "file": ctx.file_name})
                except Exception as e:
                    log_process("ANALYZE", "ERROR", f"Phase 2.1 오류 ({ctx.file_name}/{info.name}): {e}", logging.ERROR, e)
                    await results_queue.put({"type": "error", "class": info.name, "file": ctx.file_name, "message": str(e)})
                    raise

        yield emit_phase_event(2.1, "클래스 Summary 생성", "started", 0, {"classes": total})
        tasks = [asyncio.create_task(process_class(*item)) for item in all_classes]
        while completed < total:
            result = await asyncio.wait_for(results_queue.get(), timeout=300.0)
            completed += 1
            if result.get("type") == "error":
                yield emit_message(f"   ❌ [{completed}/{total}] {result['file']}/{result['class']}: {result['message'][:50]}")
            else:
                yield emit_message(f"   ✓ [{completed}/{total}] {result['file']}/{result['class']}: summary 생성")
                yield emit_phase_event(2.1, "클래스 Summary 생성", "progress", int(completed / total * 100), {"completed": completed, "total": total})
        await asyncio.gather(*tasks, return_exceptions=True)
        yield emit_phase_event(2.1, "클래스 Summary 생성", "completed", 100, {"classes": total})

    async def _run_stereotype():
        """Phase 2.2: 스테레오타입 판정"""
        all_classes = []
        for ctx in contexts:
            if ctx.status == FileStatus.PH2_OK and ctx.processor and hasattr(ctx, 'class_summaries') and ctx.class_summaries:
                for class_key, summary_data in ctx.class_summaries.items():
                    info = ctx.processor._unit_info.get(class_key)
                    if info:
                        all_classes.append((ctx, ctx.processor, class_key, info, summary_data))
        if not all_classes:
            yield emit_message("   ℹ️ Phase 2.2: 처리할 클래스 없음")
            return

        total = len(all_classes)
        completed = 0
        results_queue: asyncio.Queue = asyncio.Queue()
        semaphore = asyncio.Semaphore(settings.concurrency.max_concurrency)

        async def process_class(ctx, processor, class_key, info, summary_data):
            async with semaphore:
                try:
                    final_summary = summary_data["final_summary"]
                    class_node = summary_data["class_node"]
                    if not final_summary:
                        stereotype_result = {"class_stereotype": "", "method_stereotypes": {}}
                    else:
                        class_meta_parts = []
                        if class_node.comment:
                            class_meta_parts.append(class_node.comment)
                        if class_node.annotations:
                            class_meta_parts.append(class_node.annotations)
                        class_meta = '\n'.join(class_meta_parts) if class_meta_parts else ""
                        result = await asyncio.to_thread(
                            analyze_stereotype,
                            {"FINAL_SUMMARY": final_summary},
                            orchestrator.api_key, orchestrator.locale, class_meta
                        )
                        validated = processor.validate_dict_result(result, "스테레오타입 판정")
                        stereotype_result = {
                            "class_stereotype": validated.get('classStereotype', ''),
                            "method_stereotypes": validated.get('methodStereotypes', {}),
                        }
                        if stereotype_result["class_stereotype"]:
                            log_process("ANALYZE", "STEREOTYPE", f"   🏷️ {info.name}: {stereotype_result['class_stereotype']}")

                    class_stereotype = stereotype_result.get("class_stereotype", "")
                    method_stereotypes = stereotype_result.get("method_stereotypes", {})
                    queries = []
                    if class_stereotype:
                        queries.append({
                            "query": (
                                f"MATCH (__cy_n__:{info.kind} {{start_line: $start_line, directory: $directory, file_name: $file_name}})\n"
                                f"SET __cy_n__.stereotype = $stereotype, __cy_n__:{class_stereotype}\n"
                                "RETURN __cy_n__"
                            ),
                            "params": {
                                "start_line": info.node_start,
                                "directory": processor.full_directory,
                                "file_name": processor.file_name,
                                "stereotype": class_stereotype,
                            }
                        })
                    for method_name, method_st in method_stereotypes.items():
                        if not method_st:
                            continue
                        method_node = next(
                            (n for n in processor._nodes
                             if n.node_type in METHOD_TYPES and n.unit_key == class_key
                             and n.signature and method_name in n.signature),
                            None,
                        )
                        if method_node:
                            queries.append({
                                "query": (
                                    "MATCH (__cy_m__:METHOD {start_line: $start_line, directory: $directory, file_name: $file_name})\n"
                                    f"SET __cy_m__.stereotype = $stereotype, __cy_m__:{method_st}\n"
                                    "RETURN __cy_m__"
                                ),
                                "params": {
                                    "start_line": method_node.start_line,
                                    "directory": processor.full_directory,
                                    "file_name": processor.file_name,
                                    "stereotype": method_st,
                                }
                            })
                    if queries:
                        async with pipeline._cypher_lock:
                            async for batch_result in client.run_graph_query(queries):
                                pass
                    if not hasattr(ctx, 'stereotype_results'):
                        ctx.stereotype_results = {}
                    ctx.stereotype_results[class_key] = stereotype_result
                    await results_queue.put({"type": "success", "class": info.name, "file": ctx.file_name})
                except Exception as e:
                    log_process("ANALYZE", "ERROR", f"Phase 2.2 오류 ({ctx.file_name}/{info.name}): {e}", logging.ERROR, e)
                    await results_queue.put({"type": "error", "class": info.name, "file": ctx.file_name, "message": str(e)})
                    raise

        yield emit_phase_event(2.2, "스테레오타입 판정", "started", 0, {"classes": total})
        tasks = [asyncio.create_task(process_class(*item)) for item in all_classes]
        while completed < total:
            result = await asyncio.wait_for(results_queue.get(), timeout=300.0)
            completed += 1
            if result.get("type") == "error":
                yield emit_message(f"   ❌ [{completed}/{total}] {result['file']}/{result['class']}")
            else:
                yield emit_message(f"   ✓ [{completed}/{total}] {result['file']}/{result['class']}: 스테레오타입 판정")
                yield emit_phase_event(2.2, "스테레오타입 판정", "progress", int(completed / total * 100), {"completed": completed, "total": total})
        await asyncio.gather(*tasks, return_exceptions=True)
        yield emit_phase_event(2.2, "스테레오타입 판정", "completed", 100, {"classes": total})

    async def _run_user_story():
        """Phase 2.3: User Story 생성"""
        all_classes = []
        for ctx in contexts:
            if ctx.status == FileStatus.PH2_OK and ctx.processor and hasattr(ctx, 'class_summaries') and ctx.class_summaries:
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

        async def process_class(ctx, processor, class_key, info, summary_data):
            async with semaphore:
                try:
                    final_summary = summary_data["final_summary"]
                    if not final_summary:
                        user_stories_raw = []
                    else:
                        us_result = await asyncio.to_thread(
                            analyze_class_user_story,
                            final_summary, orchestrator.api_key, orchestrator.locale
                        )
                        validated = processor.validate_dict_result(us_result, "User Story")
                        user_stories_raw = validated.get('user_stories', [])
                    user_stories = _filter_user_stories(user_stories_raw)
                    queries = []
                    for us_idx, us in enumerate(user_stories, 1):
                        us_id = us.get('id', f"US-{us_idx}")
                        queries.append({
                            "query": (
                                f"MATCH (__cy_c__:{info.kind} {{start_line: $start_line, directory: $directory, file_name: $file_name}})\n"
                                "MERGE (__cy_us__:UserStory {id: $us_id, class_name: $class_name, directory: $directory, file_name: $file_name})\n"
                                "SET __cy_us__.role = $role, __cy_us__.goal = $goal, __cy_us__.benefit = $benefit\n"
                                "MERGE (__cy_c__)-[:HAS_USER_STORY]->(__cy_us__)\n"
                                "RETURN __cy_us__"
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
                        for ac_idx, ac in enumerate(us.get('acceptance_criteria', []), 1):
                            ac_id = ac.get('id', f"AC-{us_idx}-{ac_idx}")
                            queries.append({
                                "query": (
                                    "MATCH (__cy_us__:UserStory {id: $us_id, class_name: $class_name, directory: $directory, file_name: $file_name})\n"
                                    "MERGE (__cy_ac__:AcceptanceCriteria {id: $ac_id, user_story_id: $us_id, directory: $directory, file_name: $file_name})\n"
                                    "SET __cy_ac__.title = $ac_title, __cy_ac__.given = $ac_given, __cy_ac__.when_clause = $ac_when, __cy_ac__.then_clause = $ac_then\n"
                                    "MERGE (__cy_us__)-[:HAS_AC]->(__cy_ac__)\n"
                                    "RETURN __cy_ac__"
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
                    if queries:
                        batch_size = 30
                        for i in range(0, len(queries), batch_size):
                            batch = queries[i:i + batch_size]
                            async with pipeline._cypher_lock:
                                async for batch_result in client.run_graph_query(batch):
                                    pass
                    await results_queue.put({"type": "success", "class": info.name, "file": ctx.file_name, "user_stories": len(user_stories)})
                except Exception as e:
                    log_process("ANALYZE", "ERROR", f"Phase 2.3 오류 ({ctx.file_name}/{info.name}): {e}", logging.ERROR, e)
                    await results_queue.put({"type": "error", "class": info.name, "file": ctx.file_name, "message": str(e)})
                    raise

        yield emit_phase_event(2.3, "User Story 생성", "started", 0, {"classes": total})
        tasks = [asyncio.create_task(process_class(*item)) for item in all_classes]
        while completed < total:
            result = await asyncio.wait_for(results_queue.get(), timeout=300.0)
            completed += 1
            if result.get("type") == "error":
                yield emit_message(f"   ❌ [{completed}/{total}] {result['file']}/{result['class']}")
            else:
                us_count = result.get('user_stories', 0)
                yield emit_message(f"   ✓ [{completed}/{total}] {result['file']}/{result['class']}: User Story {us_count}개")
                yield emit_phase_event(2.3, "User Story 생성", "progress", int(completed / total * 100), {"completed": completed, "total": total})
        await asyncio.gather(*tasks, return_exceptions=True)
        yield emit_phase_event(2.3, "User Story 생성", "completed", 100, {"classes": total})

    # Phase 2.0: 메서드 보강
    async for chunk in _run_enhancement():
        yield chunk

    # Phase 2.1: 클래스 Summary
    async for chunk in _run_class_summary():
        yield chunk

    # Phase 2.2: 스테레오타입
    async for chunk in _run_stereotype():
        yield chunk

    # Phase 2.3: User Story
    async for chunk in _run_user_story():
        yield chunk
