"""Phase 3: 후처리 - DBMS

프로시저 요약, User Story, 테이블/컬럼 설명 요약을 순차 실행.
phase_llm 이후 호출.
"""

import asyncio
import logging
from typing import Any, AsyncGenerator, Dict, List, Tuple

from client.neo4j_client import Neo4jClient
from analyzer.strategy.base_analyzer import AnalysisStats
from analyzer.strategy.base.file_context import FileStatus, FileAnalysisContext
from config.settings import settings
from util.rule_loader import RuleLoader
from util.stream_event import emit_data, emit_message, emit_phase_event
from util.text_utils import calculate_code_token, log_process


# ========== RuleLoader 헬퍼 (phase_post 전용) ==========
_rule_loader_cache: Any = None

def _rule_loader() -> RuleLoader:
    global _rule_loader_cache
    if _rule_loader_cache is None:
        _rule_loader_cache = RuleLoader(target_lang="dbms")
    return _rule_loader_cache


def _analyze_summary_only(summaries: dict, api_key: str, locale: str, previous_summary: str = "") -> Dict[str, Any]:
    return _rule_loader().execute(
        "procedure_summary_only",
        {"summaries": summaries, "locale": locale, "previous_summary": previous_summary},
        api_key,
    )


def _analyze_user_story(summary: str, api_key: str, locale: str) -> Dict[str, Any]:
    return _rule_loader().execute(
        "procedure_user_story",
        {"summary": summary, "locale": locale},
        api_key,
    )


def _summarize_table_metadata(
    table_name: str,
    table_sentences: list,
    column_sentences: dict,
    column_metadata: dict,
    api_key: str,
    locale: str,
) -> Dict[str, Any]:
    return _rule_loader().execute(
        "table_summary",
        {
            "table_name": table_name,
            "table_sentences": table_sentences,
            "column_sentences": column_sentences,
            "column_metadata": column_metadata,
            "locale": locale,
        },
        api_key,
    )


# ========== 1. 프로시저 Summary ==========
def _collect_summaries_from_nodes(proc_root: Any) -> Dict[str, str]:
    """프로시저 루트의 자식 노드들에서 summary를 수집 (unit_summary_store 키 불일치 대비)"""
    result: Dict[str, str] = {}
    seen: set = set()

    def collect(node: Any) -> None:
        if id(node) in seen:
            return
        seen.add(id(node))
        if getattr(node, 'summary', None) and str(node.summary).strip():
            key = f"{node.node_type}_{node.start_line}_{node.end_line}"
            result[key] = str(node.summary).strip()
        for child in getattr(node, 'children', []) or []:
            collect(child)

    collect(proc_root)
    return result


def _split_summaries_by_token(summaries: dict, max_token: int) -> List[dict]:
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


async def _run_procedure_summary(
    pipeline: Any,
    contexts: List[FileAnalysisContext],
    client: Neo4jClient,
    orchestrator: Any,
) -> AsyncGenerator[bytes, None]:
    MAX_SUMMARY_CHUNK_TOKEN = settings.batch.max_summary_chunk_token
    success_contexts = [ctx for ctx in contexts
                       if ctx.status == FileStatus.PH2_OK
                       and ctx.processor
                       and hasattr(ctx, 'unit_summary_store')
                       and ctx.unit_summary_store]
    if not success_contexts:
        yield emit_message("   ℹ️ 프로시저 Summary: 처리할 파일 없음")
        return
    all_procedures = []
    for ctx in success_contexts:
        processor = ctx.processor
        for proc_key, info in processor._unit_info.items():
            summaries = ctx.unit_summary_store.get(proc_key, {})
            if summaries:
                proc_root = next((n for n in (processor._nodes or []) if n.unit_key == proc_key and n.node_type == info.procedure_type), None)
                if proc_root and proc_root.ok:
                    all_procedures.append((ctx, processor, proc_key, info, summaries))
    if not all_procedures:
        yield emit_message("   ℹ️ 프로시저 Summary: 처리할 프로시저 없음")
        return
    total = len(all_procedures)
    completed = 0
    results_queue: asyncio.Queue = asyncio.Queue()
    semaphore = asyncio.Semaphore(settings.concurrency.max_concurrency)

    async def process_procedure(ctx, processor, proc_key, info, summaries):
        async with semaphore:
            try:
                proc_root = next((n for n in (processor._nodes or []) if n.unit_key == proc_key and n.node_type == info.procedure_type), None)
                chunks = _split_summaries_by_token(summaries, MAX_SUMMARY_CHUNK_TOKEN)
                if not chunks and proc_root:
                    body = proc_root.get_placeholder_code()
                    if body and body.strip():
                        chunks = [{"PROCEDURE_BODY": body.strip()[:15000]}]
                if not chunks:
                    final_summary = ""
                else:
                    chunk_semaphore = asyncio.Semaphore(settings.concurrency.max_concurrency)
                    async def process_chunk(chunk):
                        async with chunk_semaphore:
                            r = await asyncio.to_thread(_analyze_summary_only, chunk, orchestrator.api_key, orchestrator.locale, "")
                            return r.get('summary', '') if isinstance(r, dict) else ""
                    chunk_results = [r for r in await asyncio.gather(*[process_chunk(c) for c in chunks]) if r]
                    if not chunk_results:
                        final_summary = ""
                    elif len(chunk_results) == 1:
                        final_summary = chunk_results[0]
                    else:
                        combined = {f"CHUNK_{i+1}": s for i, s in enumerate(chunk_results)}
                        r = await asyncio.to_thread(_analyze_summary_only, combined, orchestrator.api_key, orchestrator.locale, "")
                        final_summary = r.get('summary') or "\n\n".join(chunk_results) if isinstance(r, dict) else "\n\n".join(chunk_results)
                if not final_summary:
                    await results_queue.put({"type": "success", "procedure": info.procedure_name, "file": ctx.file_name})
                    return
                if info.schema_name:
                    cypher = f"MATCH (__cy_n__:{info.procedure_type} {{schema_name: $schema_name, procedure_name: $procedure_name, directory: $directory, file_name: $file_name}}) SET __cy_n__.summary = $summary RETURN __cy_n__"
                    params = {"schema_name": info.schema_name, "procedure_name": info.procedure_name, "directory": processor.full_directory, "file_name": processor.file_name, "summary": str(final_summary)}
                else:
                    cypher = f"MATCH (__cy_n__:{info.procedure_type} {{procedure_name: $procedure_name, directory: $directory, file_name: $file_name}}) SET __cy_n__.summary = $summary RETURN __cy_n__"
                    params = {"procedure_name": info.procedure_name, "directory": processor.full_directory, "file_name": processor.file_name, "summary": str(final_summary)}
                queries = [{"query": cypher, "params": params}]
                if queries:
                    async with pipeline._cypher_lock:
                        async for br in client.run_graph_query(queries):
                            await results_queue.put({"type": "graph", "graph": {"Nodes": br.get("Nodes", []), "Relationships": br.get("Relationships", [])}})
                if not hasattr(ctx, 'procedure_summaries'):
                    ctx.procedure_summaries = {}
                ctx.procedure_summaries[proc_key] = {"final_summary": final_summary, "procedure_info": info}
                await results_queue.put({"type": "success", "procedure": info.procedure_name, "file": ctx.file_name})
            except Exception as e:
                log_process("ANALYZE", "ERROR", f"프로시저 Summary 오류 ({ctx.file_name}/{info.procedure_name}): {e}", logging.ERROR, e)
                await results_queue.put({"type": "error", "procedure": info.procedure_name, "file": ctx.file_name, "message": str(e)})
                raise

    yield emit_phase_event(2.1, "프로시저 Summary", "started", 0, {"procedures": total})
    tasks = [asyncio.create_task(process_procedure(ctx, proc, pk, inf, summ)) for ctx, proc, pk, inf, summ in all_procedures]
    while completed < total:
        result = await asyncio.wait_for(results_queue.get(), timeout=300.0)
        if result.get("type") == "graph":
            yield emit_data(graph=result["graph"])
            continue
        completed += 1
        if result.get("type") == "error":
            yield emit_message(f"   ❌ [{completed}/{total}] {result['file']}/{result['procedure']}: {result.get('message', '')[:50]}")
        else:
            yield emit_message(f"   ✓ [{completed}/{total}] {result['file']}/{result['procedure']}: summary 생성")
        yield emit_phase_event(2.1, "프로시저 Summary", "progress", int(completed / total * 100), {"completed": completed, "total": total})
    await asyncio.gather(*tasks, return_exceptions=True)
    yield emit_phase_event(2.1, "프로시저 Summary", "completed", 100, {"procedures": total})


# ========== 2. User Story ==========
def _build_user_story_queries(procedure_type: str, procedure_name: str, directory: str, file_name: str, user_stories: List[Dict], schema_name: str | None = None) -> List[Dict]:
    queries = []
    for us_idx, us in enumerate(user_stories, 1):
        us_id = us.get('id', f"US-{us_idx}")
        if schema_name:
            match_cypher = f"MATCH (__cy_p__:{procedure_type} {{schema_name: $schema_name, procedure_name: $procedure_name, directory: $directory, file_name: $file_name}})"
            us_params = {"schema_name": schema_name, "procedure_name": procedure_name, "directory": directory, "file_name": file_name, "us_id": us_id, "role": us.get('role', ''), "goal": us.get('goal', ''), "benefit": us.get('benefit', '')}
        else:
            match_cypher = f"MATCH (__cy_p__:{procedure_type} {{procedure_name: $procedure_name, directory: $directory, file_name: $file_name}})"
            us_params = {"procedure_name": procedure_name, "directory": directory, "file_name": file_name, "us_id": us_id, "role": us.get('role', ''), "goal": us.get('goal', ''), "benefit": us.get('benefit', '')}
        queries.append({
            "query": f"{match_cypher} MERGE (__cy_us__:UserStory {{id: $us_id, procedure_name: $procedure_name, directory: $directory, file_name: $file_name}}) SET __cy_us__.role = $role, __cy_us__.goal = $goal, __cy_us__.benefit = $benefit MERGE (__cy_p__)-[__cy_r__:HAS_USER_STORY]->(__cy_us__) RETURN __cy_p__, __cy_us__, __cy_r__",
            "params": us_params,
        })
        for ac_idx, ac in enumerate(us.get('acceptance_criteria', []), 1):
            if not isinstance(ac, dict):
                continue
            ac_id = ac.get('id', f"AC-{us_idx}-{ac_idx}")
            queries.append({
                "query": f"MATCH (__cy_us__:UserStory {{id: $us_id, directory: $directory, file_name: $file_name}}) MERGE (__cy_ac__:AcceptanceCriteria {{id: $ac_id, user_story_id: $us_id, directory: $directory, file_name: $file_name}}) SET __cy_ac__.title = $title, __cy_ac__.given = $given, __cy_ac__.when_clause = $when_clause, __cy_ac__.then_clause = $then_clause MERGE (__cy_us__)-[__cy_r__:HAS_AC]->(__cy_ac__) RETURN __cy_us__, __cy_ac__, __cy_r__",
                "params": {"us_id": us_id, "ac_id": ac_id, "directory": directory, "file_name": file_name, "title": ac.get('title', ''), "given": str(ac.get('given', [])), "when_clause": str(ac.get('when', [])), "then_clause": str(ac.get('then', []))},
            })
    return queries


async def _run_user_story(
    pipeline: Any,
    contexts: List[FileAnalysisContext],
    client: Neo4jClient,
    orchestrator: Any,
) -> AsyncGenerator[bytes, None]:
    all_procedures = []
    for ctx in contexts:
        if ctx.status == FileStatus.PH2_OK and ctx.processor and hasattr(ctx, 'procedure_summaries') and ctx.procedure_summaries:
            for proc_key, summary_data in ctx.procedure_summaries.items():
                info = summary_data.get("procedure_info")
                if info:
                    all_procedures.append((ctx, ctx.processor, proc_key, info, summary_data))
    if not all_procedures:
        yield emit_message("   ℹ️ User Story: 처리할 프로시저 없음")
        return
    total = len(all_procedures)
    completed = 0
    results_queue: asyncio.Queue = asyncio.Queue()
    semaphore = asyncio.Semaphore(settings.concurrency.max_concurrency)

    async def process_procedure(ctx, processor, proc_key, info, summary_data):
        async with semaphore:
            try:
                final_summary = summary_data["final_summary"]
                if not final_summary:
                    user_stories_raw = []
                else:
                    r = await asyncio.to_thread(_analyze_user_story, final_summary, orchestrator.api_key, orchestrator.locale)
                    user_stories_raw = r.get('user_stories', []) or [] if isinstance(r, dict) else []
                queries = []
                if user_stories_raw:
                    queries = _build_user_story_queries(info.procedure_type, info.procedure_name, processor.full_directory, processor.file_name, user_stories_raw, info.schema_name)
                if queries:
                    for i in range(0, len(queries), 30):
                        batch = queries[i:i + 30]
                        async with pipeline._cypher_lock:
                            async for br in client.run_graph_query(batch):
                                await results_queue.put({"type": "graph", "graph": {"Nodes": br.get("Nodes", []), "Relationships": br.get("Relationships", [])}})
                await results_queue.put({"type": "success", "procedure": info.procedure_name, "file": ctx.file_name, "user_stories": len(user_stories_raw)})
            except Exception as e:
                log_process("ANALYZE", "ERROR", f"User Story 오류 ({ctx.file_name}/{info.procedure_name}): {e}", logging.ERROR, e)
                await results_queue.put({"type": "error", "procedure": info.procedure_name, "file": ctx.file_name, "message": str(e)})
                raise

    yield emit_phase_event(2.2, "User Story", "started", 0, {"procedures": total})
    tasks = [asyncio.create_task(process_procedure(ctx, proc, pk, inf, sd)) for ctx, proc, pk, inf, sd in all_procedures]
    while completed < total:
        result = await asyncio.wait_for(results_queue.get(), timeout=300.0)
        if result.get("type") == "graph":
            yield emit_data(graph=result["graph"])
            continue
        completed += 1
        if result.get("type") == "error":
            yield emit_message(f"   ❌ [{completed}/{total}] {result['file']}/{result['procedure']}: {result.get('message', '')[:50]}")
        else:
            yield emit_message(f"   ✓ [{completed}/{total}] {result['file']}/{result['procedure']}: User Story {result.get('user_stories', 0)}개")
        yield emit_phase_event(2.2, "User Story", "progress", int(completed / total * 100), {"completed": completed, "total": total})
    await asyncio.gather(*tasks, return_exceptions=True)
    yield emit_phase_event(2.2, "User Story", "completed", 100, {"procedures": total})


# ========== 3. 테이블/컬럼 설명 요약 ==========
async def _summarize_table(
    table_key: Tuple[str, str],
    data: Dict,
    ddl_table_metadata: Dict,
    dbms: str,
    api_key: str,
    locale: str,
) -> List[Dict]:
    schema_key, name_key = table_key
    ddl_key = (schema_key.lower(), name_key.lower())
    ddl_meta = ddl_table_metadata.get(ddl_key, {})
    ddl_description = (ddl_meta.get('description') or '').strip()
    ddl_columns = ddl_meta.get('columns') or {}
    summaries = list(data.get('summaries') or [])
    columns_map = data.get('columns') or {}
    column_sentences = {entry['name']: list(entry['summaries']) for entry in columns_map.values() if entry.get('summaries')}
    if ddl_description:
        summaries.insert(0, f"[DDL 메타데이터] {ddl_description}")
    for col_name, ddl_col in ddl_columns.items():
        ddl_col_desc = (ddl_col.get('description') or '').strip()
        if ddl_col_desc and col_name not in column_sentences:
            column_sentences[col_name] = [f"[DDL 메타데이터] {ddl_col_desc}"]
        elif ddl_col_desc and col_name in column_sentences:
            column_sentences[col_name].insert(0, f"[DDL 메타데이터] {ddl_col_desc}")
    if not summaries and not column_sentences:
        return []
    table_display = f"{schema_key}.{name_key}" if schema_key else name_key
    column_metadata = {}
    for entry in columns_map.values():
        col_name = entry['name']
        ddl_col = ddl_columns.get(col_name, {})
        column_metadata[col_name] = {"dtype": entry.get("dtype") or ddl_col.get("dtype") or "", "nullable": bool(entry.get("nullable", True)) if entry.get("nullable") is not None else ddl_col.get("nullable", True), "examples": sorted(list(entry.get("examples") or []))[:5]}
    for col_name, ddl_col in ddl_columns.items():
        if col_name not in column_metadata:
            column_metadata[col_name] = {"dtype": ddl_col.get("dtype") or "", "nullable": ddl_col.get("nullable", True), "examples": []}
    result = await asyncio.to_thread(_summarize_table_metadata, table_display, summaries, column_sentences, column_metadata, api_key, locale)
    if not isinstance(result, dict):
        raise RuntimeError(f"테이블 요약 결과 오류 ({schema_key}.{name_key})")
    queries = []
    llm_table_desc = (result.get('tableDescription') or '').strip()
    if llm_table_desc:
        queries.append({"query": "MATCH (__cy_t__:Table {schema: $schema, name: $name, db: $db}) SET __cy_t__.analyzed_description = $llm_desc WITH __cy_t__ WHERE __cy_t__.description IS NULL OR __cy_t__.description = '' SET __cy_t__.description = $llm_desc, __cy_t__.description_source = 'procedure' RETURN __cy_t__", "params": {"schema": schema_key, "name": name_key, "db": dbms, "llm_desc": llm_table_desc}})
    for column_info in result.get('columns', []) or []:
        column_name = (column_info.get('name') or '').strip()
        llm_column_desc = (column_info.get('description') or '').strip()
        if not column_name or not llm_column_desc:
            continue
        fqn = '.'.join(filter(None, [schema_key, name_key, column_name])).lower()
        queries.append({"query": "MATCH (__cy_c__:Column {fqn: $fqn}) SET __cy_c__.analyzed_description = $llm_desc WITH __cy_c__ WHERE __cy_c__.description IS NULL OR __cy_c__.description = '' SET __cy_c__.description = $llm_desc, __cy_c__.description_source = 'procedure' RETURN __cy_c__", "params": {"fqn": fqn, "llm_desc": llm_column_desc}})
    return queries


async def _run_table_summary(
    pipeline: Any,
    contexts: List[FileAnalysisContext],
    client: Neo4jClient,
    orchestrator: Any,
) -> AsyncGenerator[bytes, None]:
    all_table_stores = {}
    all_ddl_metadata = {}
    dbms_name = None
    for ctx in contexts:
        if ctx.status == FileStatus.PH2_OK and ctx.processor:
            proc = ctx.processor
            if hasattr(proc, '_table_summary_store') and proc._table_summary_store:
                for table_key, data in proc._table_summary_store.items():
                    if table_key not in all_table_stores:
                        all_table_stores[table_key] = {"summaries": set(), "columns": {}}
                    all_table_stores[table_key]["summaries"].update(data.get("summaries", set()))
                    for col_name, col_data in data.get("columns", {}).items():
                        if col_name not in all_table_stores[table_key]["columns"]:
                            all_table_stores[table_key]["columns"][col_name] = col_data.copy()
                        else:
                            all_table_stores[table_key]["columns"][col_name]["summaries"].update(col_data.get("summaries", set()))
            if hasattr(proc, '_ddl_table_metadata') and proc._ddl_table_metadata:
                all_ddl_metadata.update(proc._ddl_table_metadata)
            if not dbms_name and hasattr(proc, 'dbms'):
                dbms_name = proc.dbms
    if not all_table_stores:
        yield emit_phase_event(2.4, "테이블/컬럼 설명 요약", "completed", 100, {"tables": 0})
        return
    yield emit_phase_event(2.4, "테이블/컬럼 설명 요약", "started", 0, {"tables": len(all_table_stores)})
    tasks = [_summarize_table(table_key, {"summaries": list(data["summaries"]), "columns": data["columns"]}, all_ddl_metadata, dbms_name or "", orchestrator.api_key, orchestrator.locale) for table_key, data in all_table_stores.items()]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    all_queries = []
    completed = 0
    for r in results:
        completed += 1
        if isinstance(r, Exception):
            yield emit_message(f"   ❌ [{completed}/{len(tasks)}] 테이블 요약 오류: {str(r)[:50]}")
        elif r:
            all_queries.extend(r)
            yield emit_message(f"   ✓ [{completed}/{len(tasks)}] 테이블 요약 완료")
        yield emit_phase_event(2.4, "테이블/컬럼 설명 요약", "progress", int(completed / len(tasks) * 100), {"completed": completed, "total": len(tasks)})
    if all_queries:
        async with pipeline._cypher_lock:
            async for br in client.run_graph_query(all_queries):
                yield emit_data(graph={"Nodes": br.get("Nodes", []), "Relationships": br.get("Relationships", [])})
    for ctx in contexts:
        if ctx.processor and hasattr(ctx.processor, '_table_summary_store'):
            ctx.processor._table_summary_store.clear()
    yield emit_phase_event(2.4, "테이블/컬럼 설명 요약", "completed", 100, {"tables": len(tasks), "queries": len(all_queries)})


# ========== Phase 3 통합 진입점 ==========
async def run_phase_post(
    pipeline: Any,
    contexts: List[FileAnalysisContext],
    client: Neo4jClient,
    orchestrator: Any,
    stats: AnalysisStats,
) -> AsyncGenerator[bytes, None]:
    """Phase 3: 후처리 - 프로시저 요약 → User Story → 테이블 요약"""
    async for chunk in _run_procedure_summary(pipeline, contexts, client, orchestrator):
        yield chunk
    async for chunk in _run_user_story(pipeline, contexts, client, orchestrator):
        yield chunk
    async for chunk in _run_table_summary(pipeline, contexts, client, orchestrator):
        yield chunk
