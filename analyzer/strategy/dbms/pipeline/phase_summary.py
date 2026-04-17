"""Phase 3: 후처리 - DBMS

프로시저 요약, User Story, 테이블/컬럼 설명 요약을 순차 실행.
phase_llm 이후 호출.
"""

import asyncio
import logging
from typing import Any, AsyncGenerator, Dict, List, Tuple

from client.neo4j_client import Neo4jClient
from analyzer.strategy.dbms.common.node_models import FileStatus, FileAnalysisContext
from analyzer.common.analysis_context import fetch_constants_context, fetch_table_context
from config.settings import settings
from util.stream_emitter import emit_data, emit_message, emit_phase_event
from util.logger import log_process
from util.code_text_utils import calculate_code_token
from analyzer.common.graph_writer import run_graph_queries


# ========== PromptRegistry 기반 LLM 호출 ==========
from util.llm_prompt_handler import prompts


def _collect_variable_declarations(processor: Any, proc_key: str, var_types: frozenset) -> str:
    """프로시저 내 DECLARE 블록의 변수 선언/초기값을 수집."""
    parts = []
    for node in (processor._nodes or []):
        if node.unit_key == proc_key and node.node_type in var_types and node.code:
            code_line = " ".join(node.code.strip().split())
            if code_line:
                parts.append(code_line)
    return " | ".join(parts)


def _analyze_summary_only(
    summaries: dict,
    api_key: str,
    locale: str,
    variable_declarations: str = "",
    table_sample_data: str = "",
    constants_context: str = "",
    called_procedure_summaries: str = "",
) -> Dict[str, Any]:
    inputs: Dict[str, Any] = {"summaries": summaries, "locale": locale}
    if variable_declarations:
        inputs["variable_declarations"] = variable_declarations
    if table_sample_data:
        inputs["table_sample_data"] = table_sample_data
    if constants_context:
        inputs["constants_context"] = constants_context
    if called_procedure_summaries:
        inputs["called_procedure_summaries"] = called_procedure_summaries
    return prompts.execute_raw("dbms", "unit_analysis", inputs, api_key)


def _summarize_table_metadata(
    table_name: str,
    table_sentences: list,
    column_sentences: dict,
    column_metadata: dict,
    api_key: str,
    locale: str,
) -> Dict[str, Any]:
    return prompts.execute_raw(
        "dbms", "table_summary",
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


async def _run_unit_analysis(
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

    constants_context = ""
    try:
        constants_context = await fetch_constants_context(client)
    except Exception as e:
        log_process("PHASE:2", "ERROR", f"상수 컨텍스트 수집 실패: {e}")


    async def _collect_table_names(proc_name: str, directory: str, file_name: str) -> list[str]:
        """프로시저가 접근하는 테이블명 목록을 수집."""
        try:
            query = (
                "MATCH (p {procedure_name: $proc_name, directory: $directory, file_name: $file_name})"
                "-[:PARENT_OF*]->(dml)-[:READS_FROM|WRITES_TO]->(t:Table) "
                "RETURN DISTINCT t.name AS table_name"
            )
            params = {"proc_name": proc_name, "directory": directory, "file_name": file_name}
            results = await client.execute_queries([query], params)
            rows = results[0] if results and results[0] else []
            return [r.get("table_name") for r in rows if r.get("table_name")]
        except Exception as e:
            log_process("PHASE:2", "ERROR", f"테이블명 수집 실패 ({proc_name}): {e}")
            return []

    async def _collect_called_procedure_summaries(proc_name: str, directory: str, file_name: str, max_items: int = 20) -> str:
        """CALLS 관계로 연결된 프로시저들의 summary를 수집."""
        try:
            query = (
                "MATCH (p {procedure_name: $proc_name, directory: $directory, file_name: $file_name})"
                "-[:PARENT_OF*]->(node)-[:CALLS]->(callee) "
                "WHERE callee.summary IS NOT NULL AND callee.summary <> '' "
                "RETURN DISTINCT callee.procedure_name AS name, callee.summary AS summary "
                "ORDER BY name"
            )
            params = {"proc_name": proc_name, "directory": directory, "file_name": file_name}
            results = await client.execute_queries([query], params)
            rows = results[0] if results and results[0] else []
            parts: list[str] = []
            for r in rows[:max_items]:
                name = (r.get("name") or "").strip()
                summ = (r.get("summary") or "").strip()
                if not summ:
                    continue
                parts.append(f"- {name}: {summ}" if name else f"- {summ}")
            if not parts:
                return ""
            return "\n".join(parts)
        except Exception as e:
            log_process("PHASE:2", "ERROR", f"호출 프로시저 요약 수집 실패 ({proc_name}): {e}")
            return ""

    async def process_procedure(ctx, processor, proc_key, info, summaries):
        async with semaphore:
            try:
                from analyzer.strategy.dbms.common.analysis_constants import VARIABLE_DECLARATION_TYPES
                proc_root = next((n for n in (processor._nodes or []) if n.unit_key == proc_key and n.node_type == info.procedure_type), None)

                # 변수 선언/초기값 수집
                var_decls = _collect_variable_declarations(processor, proc_key, VARIABLE_DECLARATION_TYPES)

                # 테이블 스키마 정보 수집
                table_names = await _collect_table_names(info.procedure_name, processor.full_directory, processor.file_name)
                datasource_name = getattr(orchestrator, "datasource_name", "")
                table_data = await fetch_table_context(client, table_names, datasource_name) if table_names else ""

                # 호출된 프로시저 요약 수집 (CALL 관계)
                called_procedure_summaries = await _collect_called_procedure_summaries(
                    info.procedure_name, processor.full_directory, processor.file_name
                )

                # 스켈레톤+리프 방식: 중간 노드는 원문, 리프만 summary로 대체
                # → 중복 없음, 제어 흐름 보존, 요약의 요약 방지
                if proc_root:
                    deep_compact = proc_root.get_deep_compact_code()
                    if deep_compact and deep_compact.strip():
                        chunks = _split_summaries_by_token(
                            {"PROCEDURE_COMPACT": deep_compact.strip()}, MAX_SUMMARY_CHUNK_TOKEN
                        )
                    else:
                        chunks = _split_summaries_by_token(summaries, MAX_SUMMARY_CHUNK_TOKEN)
                else:
                    chunks = _split_summaries_by_token(summaries, MAX_SUMMARY_CHUNK_TOKEN)
                if not chunks:
                    final_summary = ""
                    final_scenarios = []
                else:
                    chunk_semaphore = asyncio.Semaphore(settings.concurrency.max_concurrency)
                    async def process_chunk(chunk):
                        async with chunk_semaphore:
                            r = await asyncio.to_thread(
                                _analyze_summary_only, chunk, orchestrator.api_key, orchestrator.locale,
                                variable_declarations=var_decls,
                                table_sample_data=table_data,
                                constants_context=constants_context,
                                called_procedure_summaries=called_procedure_summaries,
                            )
                            return r if isinstance(r, dict) else {}
                    chunk_results_raw = await asyncio.gather(*[process_chunk(c) for c in chunks])
                    chunk_summaries = [r.get('summary', '') for r in chunk_results_raw if r.get('summary')]
                    # 최종 chunk에서만 scenarios 수집
                    final_scenarios = []
                    for r in chunk_results_raw:
                        if r.get('scenarios'):
                            final_scenarios = r['scenarios']

                    if not chunk_summaries:
                        final_summary = ""
                    elif len(chunk_summaries) == 1:
                        final_summary = chunk_summaries[0]
                    else:
                        combined = {f"CHUNK_{i+1}": s for i, s in enumerate(chunk_summaries)}
                        r = await asyncio.to_thread(
                            _analyze_summary_only, combined, orchestrator.api_key, orchestrator.locale,
                            variable_declarations=var_decls,
                            table_sample_data=table_data,
                            constants_context=constants_context,
                            called_procedure_summaries=called_procedure_summaries,
                        )
                        if isinstance(r, dict):
                            final_summary = r.get('summary') or "\n\n".join(chunk_summaries)
                            if r.get('scenarios'):
                                final_scenarios = r['scenarios']
                        else:
                            final_summary = "\n\n".join(chunk_summaries)

                if not final_summary:
                    await results_queue.put({"type": "success", "procedure": info.procedure_name, "file": ctx.file_name})
                    return

                # Summary 저장
                if info.schema_name:
                    cypher = f"MATCH (__cy_n__:{info.procedure_type} {{schema_name: $schema_name, procedure_name: $procedure_name, directory: $directory, file_name: $file_name}}) SET __cy_n__.summary = $summary RETURN __cy_n__"
                    params = {"schema_name": info.schema_name, "procedure_name": info.procedure_name, "directory": processor.full_directory, "file_name": processor.file_name, "summary": str(final_summary)}
                else:
                    cypher = f"MATCH (__cy_n__:{info.procedure_type} {{procedure_name: $procedure_name, directory: $directory, file_name: $file_name}}) SET __cy_n__.summary = $summary RETURN __cy_n__"
                    params = {"procedure_name": info.procedure_name, "directory": processor.full_directory, "file_name": processor.file_name, "summary": str(final_summary)}
                queries = [{"query": cypher, "params": params}]
                # BusinessLogic 노드 생성
                if final_scenarios:
                    proc_id = f"{processor.full_directory}/{processor.file_name}:{info.procedure_name}"
                    for idx, sc in enumerate(final_scenarios):
                        if isinstance(sc, dict) and sc.get("title"):
                            seq = sc.get("sequence", idx + 1)
                            coupled_domain = sc.get("coupled_domain")
                            if info.schema_name:
                                bl_cypher = f"MATCH (p:{info.procedure_type} {{schema_name: $schema_name, procedure_name: $procedure_name, directory: $directory, file_name: $file_name}}) MERGE (g:BusinessLogic {{procedure_id: $proc_id, title: $title}}) SET g.sequence = $sequence, g.coupled_domain = $coupled_domain, g.given = $given, g.when = $when, g.then = $then, g.updated_at = timestamp() MERGE (p)-[:HAS_BUSINESS_LOGIC]->(g) RETURN p, g"
                                bl_params = {"schema_name": info.schema_name, "procedure_name": info.procedure_name, "directory": processor.full_directory, "file_name": processor.file_name, "proc_id": proc_id, "sequence": seq, "coupled_domain": coupled_domain, "title": sc.get("title", ""), "given": sc.get("given", ""), "when": sc.get("when", ""), "then": sc.get("then", "")}
                            else:
                                bl_cypher = f"MATCH (p:{info.procedure_type} {{procedure_name: $procedure_name, directory: $directory, file_name: $file_name}}) MERGE (g:BusinessLogic {{procedure_id: $proc_id, title: $title}}) SET g.sequence = $sequence, g.coupled_domain = $coupled_domain, g.given = $given, g.when = $when, g.then = $then, g.updated_at = timestamp() MERGE (p)-[:HAS_BUSINESS_LOGIC]->(g) RETURN p, g"
                                bl_params = {"procedure_name": info.procedure_name, "directory": processor.full_directory, "file_name": processor.file_name, "proc_id": proc_id, "sequence": seq, "coupled_domain": coupled_domain, "title": sc.get("title", ""), "given": sc.get("given", ""), "when": sc.get("when", ""), "then": sc.get("then", "")}
                            queries.append({"query": bl_cypher, "params": bl_params})
                if queries:
                    async for batch in run_graph_queries(client, queries, cypher_lock=pipeline._cypher_lock):
                        await results_queue.put({"type": "graph", "graph": batch})
                await results_queue.put({"type": "success", "procedure": info.procedure_name, "file": ctx.file_name})
            except Exception as e:
                log_process("PHASE:2", "ERROR", f"프로시저 Summary 오류 ({ctx.file_name}/{info.procedure_name}): {e}", logging.ERROR, e)
                await results_queue.put({"type": "error", "procedure": info.procedure_name, "file": ctx.file_name, "message": str(e)})
                raise

    yield emit_phase_event(2, "AI 분석", "in_progress", 60, {"step": "프로시저 Summary", "total": total})
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
        yield emit_phase_event(2, "AI 분석", "in_progress", 60 + int(completed / total * 15), {"step": "프로시저 Summary", "done": completed, "total": total})
    await asyncio.gather(*tasks, return_exceptions=True)
    yield emit_phase_event(2, "AI 분석", "in_progress", 75, {"step": "프로시저 Summary 완료", "total": total})


# ========== 2. 테이블/컬럼 설명 요약 ==========
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
        queries.append({"query": "MATCH (__cy_t__:Table {schema: $schema, name: $name, db: $db}) SET __cy_t__.analyzed_description = $llm_desc WITH __cy_t__ WHERE __cy_t__.description IS NULL OR __cy_t__.description = '' SET __cy_t__.description = $llm_desc, __cy_t__.description_source = 'code' RETURN __cy_t__", "params": {"schema": schema_key, "name": name_key, "db": dbms, "llm_desc": llm_table_desc}})
    for column_info in result.get('columns', []) or []:
        column_name = (column_info.get('name') or '').strip()
        llm_column_desc = (column_info.get('description') or '').strip()
        if not column_name or not llm_column_desc:
            continue
        fqn = '.'.join(filter(None, [schema_key, name_key, column_name])).lower()
        queries.append({"query": "MATCH (__cy_c__:Column {fqn: $fqn}) SET __cy_c__.analyzed_description = $llm_desc WITH __cy_c__ WHERE __cy_c__.description IS NULL OR __cy_c__.description = '' SET __cy_c__.description = $llm_desc, __cy_c__.description_source = 'code' RETURN __cy_c__", "params": {"fqn": fqn, "llm_desc": llm_column_desc}})
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
        yield emit_phase_event(2, "AI 분석", "in_progress", 100, {"step": "테이블 요약 완료", "tables": 0})
        return
    yield emit_phase_event(2, "AI 분석", "in_progress", 85, {"step": "테이블/컬럼 설명 요약", "total": len(all_table_stores)})
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
        yield emit_phase_event(2, "AI 분석", "in_progress", 85 + int(completed / len(tasks) * 15), {"step": "테이블/컬럼 설명 요약", "done": completed, "total": len(tasks)})
    if all_queries:
        async for batch in run_graph_queries(client, all_queries, cypher_lock=pipeline._cypher_lock):
            yield emit_data(graph=batch)
    for ctx in contexts:
        if ctx.processor and hasattr(ctx.processor, '_table_summary_store'):
            ctx.processor._table_summary_store.clear()
    yield emit_phase_event(2, "AI 분석", "in_progress", 100, {"step": "테이블/컬럼 설명 요약 완료", "tables": len(tasks), "queries": len(all_queries)})


# ========== Phase 3 통합 진입점 ==========
async def run_phase_post(
    pipeline: Any,
    contexts: List[FileAnalysisContext],
    client: Neo4jClient,
    orchestrator: Any,
) -> AsyncGenerator[bytes, None]:
    """Phase 3: 후처리 - 프로시저 요약 → 테이블 요약"""
    async for chunk in _run_unit_analysis(pipeline, contexts, client, orchestrator):
        yield chunk
    async for chunk in _run_table_summary(pipeline, contexts, client, orchestrator):
        yield chunk
