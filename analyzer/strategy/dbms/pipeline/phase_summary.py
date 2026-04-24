"""Phase 3: 후처리 - DBMS

프로시저 요약, User Story, 테이블/컬럼 설명 요약을 순차 실행.
phase_llm 이후 호출.
"""

import asyncio
import logging
from typing import Any, AsyncGenerator, Dict, List, Tuple

from analyzer.strategy.dbms.common.node_models import FileStatus, FileAnalysisContext
from analyzer.common.analysis_context import fetch_constants_context
from analyzer.common.example_collector import collect_example_rows
from config.settings import settings
from util.stream_emitter import emit_data, emit_message, emit_phase_event
from util.logger import log_process
from util.code_text_utils import calculate_code_token
from analyzer.common.graph_writer import run_graph_queries


# ========== PromptRegistry 기반 LLM 호출 ==========
from util.llm_prompt_handler import prompts
from util.llm_rate_limiter import get_executor
from analyzer.strategy.framework.common.graph_queries import _rule_hash


async def _run_chunks_continuous(chunks: List[Any], processor_fn, label: str) -> List[Any]:
    """chunk 처리를 AsyncBatchExecutor 로 실행 — 워커 풀 continuous, 결과는 완료순 수집.

    순수 LLM 병렬 사이트의 표준 통일 패턴.
    """
    executor = get_executor()
    results: List[Any] = []
    def _on(r):
        if r is not None:
            results.append(r)
    await executor.run_batch(items=chunks, processor=processor_fn, on_result=_on, label=label)
    return results


# ── 프로시저 요약 쿼리 빌더 ─────────────────────────────────────────────
#
# PROCEDURE/FUNCTION/TRIGGER 노드에 summary 저장 + Example Mapping 3요소
# (Rule/Example/Question) 및 Rule 간 NEXT/BRANCH 관계 생성.
# Framework 의 build_analysis_result_queries 와 동일한 스키마·해시·관계 구조를
# DBMS 프로시저 앵커(composite key) 에 맞게 구성.

def _proc_match_clause(info: Any) -> Tuple[str, Dict[str, Any]]:
    """PROCEDURE/FUNCTION/TRIGGER 노드 composite-key MATCH 절 + 파라미터."""
    if info.schema_name:
        match = (
            f"MATCH (p:{info.procedure_type} "
            f"{{schema_name: $schema_name, procedure_name: $procedure_name, "
            f"directory: $directory, file_name: $file_name}})"
        )
        params = {"schema_name": info.schema_name, "procedure_name": info.procedure_name}
    else:
        match = (
            f"MATCH (p:{info.procedure_type} "
            f"{{procedure_name: $procedure_name, directory: $directory, file_name: $file_name}})"
        )
        params = {"procedure_name": info.procedure_name}
    return match, params


def _build_procedure_summary_queries(
    processor: Any,
    info: Any,
    summary: str,
    analysis: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """프로시저 1개에 대한 Summary + EM 쿼리 리스트 반환.

    흐름:
      1) PROCEDURE.summary SET
      2) Rule 노드 (rule_hash 공유) + HAS_RULE 관계
      3) Example 노드 + HAS_EXAMPLE
      4) Question 노드 + HAS_QUESTION
      5) Rule 간 NEXT / BRANCH
    """
    proc_match, proc_key_params = _proc_match_clause(info)
    base_params = {
        **proc_key_params,
        "directory": processor.full_directory,
        "file_name": processor.file_name,
    }
    proc_id = f"{processor.full_directory}/{processor.file_name}:{info.procedure_name}"
    queries: List[Dict[str, Any]] = [{
        "query": f"{proc_match} SET p.summary = $summary RETURN p",
        "params": {**base_params, "summary": str(summary)},
    }]

    # 수집: Rule / Example / Question / Flow
    rule_rows: List[Dict[str, Any]] = []
    question_rows: List[Dict[str, Any]] = []
    flow_by_rid: Dict[str, str] = {}
    branch_by_rid: Dict[str, Any] = {}

    for r in (analysis.get("rules") or []):
        if not isinstance(r, dict):
            continue
        rid = str(r.get("rule_id", "")).strip()
        statement = (r.get("statement") or "").strip()
        if not rid or not statement:
            continue
        flow_id = str(r.get("flow_id", "")).strip() or rid
        branch_from = r.get("branch_from") or None
        flow_by_rid[rid] = flow_id
        branch_by_rid[rid] = branch_from
        rule_rows.append({
            "rule_id": rid,
            "rule_hash": _rule_hash(statement),
            "statement": statement,
            "flow_id": flow_id,
            "guard_rule_id": r.get("guard_rule_id") or None,
            "branch_from": branch_from,
            "coupled_domains": [str(d).strip() for d in (r.get("coupled_domains") or []) if str(d).strip()],
        })

    # Example 수집 (given/then dict→JSON, then.writes[]→AFFECTS_TABLE 엣지)
    example_rows, example_write_edges = collect_example_rows(
        analysis.get("examples") or [],
        valid_rule_ids=flow_by_rid.keys(),
        example_id_factory=lambda rid, idx: f"{proc_id}::{rid}::{idx}",
    )

    for idx, q in enumerate(analysis.get("questions") or [], start=1):
        if not isinstance(q, dict):
            continue
        text = (q.get("text") or "").strip()
        if not text:
            continue
        question_rows.append({
            "question_id": f"{proc_id}::Q{idx}",
            "text": text,
            "reason": (q.get("reason") or "").strip(),
        })

    # Rule
    if rule_rows:
        queries.append({
            "query": (
                f"{proc_match}\n"
                "UNWIND $rows AS row\n"
                "MERGE (r:Rule {rule_hash: row.rule_hash})\n"
                "  ON CREATE SET r.statement = row.statement, r.created_at = timestamp()\n"
                "MERGE (p)-[rel:HAS_RULE {local_id: row.rule_id}]->(r)\n"
                "SET rel.flow_id = row.flow_id,\n"
                "    rel.guard_rule_id = row.guard_rule_id,\n"
                "    rel.branch_from = row.branch_from,\n"
                "    rel.coupled_domains = row.coupled_domains,\n"
                "    rel.updated_at = timestamp()\n"
                "RETURN p, rel, r"
            ),
            "params": {**base_params, "rows": rule_rows},
        })

    # Example (given/then 은 하이브리드 JSON 직렬화 문자열)
    if example_rows:
        queries.append({
            "query": (
                f"{proc_match}\n"
                "UNWIND $rows AS row\n"
                "MATCH (p)-[:HAS_RULE {local_id: row.rule_id}]->(r:Rule)\n"
                "MERGE (e:Example {example_id: row.example_id})\n"
                "SET e.description = row.description,\n"
                "    e.given = row.given, e.when_ = row.when_, e.then_ = row.then_,\n"
                "    e.is_boundary = row.is_boundary, e.updated_at = timestamp()\n"
                "MERGE (r)-[:HAS_EXAMPLE]->(e)\n"
                "RETURN r, e"
            ),
            "params": {**base_params, "rows": example_rows},
        })

    # Example → Table AFFECTS_TABLE 엣지
    if example_write_edges:
        queries.append({
            "query": (
                "UNWIND $rows AS row\n"
                "MATCH (e:Example {example_id: row.example_id})\n"
                "MATCH (t:Table) WHERE toLower(t.name) = toLower(row.table_name)\n"
                "MERGE (e)-[rel:AFFECTS_TABLE {op: row.op}]->(t)\n"
                "SET rel.updated_at = timestamp()\n"
                "RETURN e, rel, t"
            ),
            "params": {"rows": example_write_edges},
        })

    # Question
    if question_rows:
        queries.append({
            "query": (
                f"{proc_match}\n"
                "UNWIND $rows AS row\n"
                "MERGE (q:Question {question_id: row.question_id})\n"
                "  ON CREATE SET q.text = row.text, q.reason = row.reason, q.created_at = timestamp()\n"
                "  ON MATCH  SET q.text = row.text, q.reason = row.reason, q.updated_at = timestamp()\n"
                "MERGE (p)-[:HAS_QUESTION]->(q)\n"
                "RETURN p, q"
            ),
            "params": {**base_params, "rows": question_rows},
        })

    # Rule 간 흐름(NEXT/BRANCH) 관계
    main_rids = sorted(
        (rid for rid, flow in flow_by_rid.items() if "-" not in flow),
        key=lambda rid: flow_by_rid[rid],
    )
    next_edges = [
        {"from_rule_id": main_rids[i], "to_rule_id": main_rids[i + 1]}
        for i in range(len(main_rids) - 1)
    ]
    flow_to_rid = {flow: rid for rid, flow in flow_by_rid.items()}
    branch_edges = []
    for rid, flow in flow_by_rid.items():
        if "-" not in flow:
            continue
        parent_rid = branch_by_rid.get(rid) or flow_to_rid.get(flow.rsplit("-", 1)[0])
        if parent_rid and parent_rid in flow_by_rid:
            branch_edges.append({"from_rule_id": parent_rid, "to_rule_id": rid})

    for rel_type, edges in (("NEXT", next_edges), ("BRANCH", branch_edges)):
        if edges:
            queries.append({
                "query": (
                    f"{proc_match}\n"
                    "UNWIND $rows AS row\n"
                    "MATCH (p)-[:HAS_RULE {local_id: row.from_rule_id}]->(a:Rule)\n"
                    "MATCH (p)-[:HAS_RULE {local_id: row.to_rule_id}]->(b:Rule)\n"
                    f"MERGE (a)-[:{rel_type} {{procedure_id: $proc_id}}]->(b)"
                ),
                "params": {**base_params, "proc_id": proc_id, "rows": edges},
            })

    return queries


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
    constants_context: str = "",
    called_procedure_summaries: str = "",
) -> Dict[str, Any]:
    """unit_analysis 호출 — procedure 전체 summary 한 편만 생성."""
    inputs: Dict[str, Any] = {"summaries": summaries, "locale": locale}
    if variable_declarations:
        inputs["variable_declarations"] = variable_declarations
    if constants_context:
        inputs["constants_context"] = constants_context
    if called_procedure_summaries:
        inputs["called_procedure_summaries"] = called_procedure_summaries
    return prompts.execute_raw("dbms", "unit_analysis", inputs, api_key)


def _build_final_analysis(proc_root: Any) -> Dict[str, Any]:
    """proc_root.analysis_map (Bottom-up 결과) → phase_summary 가 쓰는 analysis dict.

    Python 결정론 후처리:
    - rule_id 순번 부여 (R1, R2, ...)
    - statement 조립 (condition + ' 이면 ' + result, condition 없으면 result 만)
    - Rule 안 nested examples 를 flat examples 리스트로 분리 (rule_id 연결)
    - flow_id 는 기본 순번, guard/branch/coupled_domains 는 기본값 (필요 시 확장)
    """
    if not proc_root or not getattr(proc_root, "analysis_map", None):
        return {"rules": [], "examples": [], "questions": []}

    raw_rules = proc_root.analysis_map.get("rules") or []
    raw_questions = proc_root.analysis_map.get("questions") or []

    final_rules: List[Dict[str, Any]] = []
    final_examples: List[Dict[str, Any]] = []

    for idx, r in enumerate(raw_rules, start=1):
        if not isinstance(r, dict):
            continue
        rule_id = f"R{idx}"
        cond = (r.get("condition") or "").strip()
        result = (r.get("result") or "").strip()
        if not result:
            continue
        statement = f"{cond} 이면 {result}" if cond else result

        final_rules.append({
            "rule_id": rule_id,
            "flow_id": str(idx),
            "guard_rule_id": None,
            "branch_from": None,
            "statement": statement,
            "coupled_domains": [],
        })

        # Rule 안 nested examples → flat 리스트로 분리, rule_id 연결
        for ex in (r.get("examples") or []):
            if not isinstance(ex, dict):
                continue
            ex_copy = dict(ex)
            ex_copy["rule_id"] = rule_id
            final_examples.append(ex_copy)

    return {
        "rules": final_rules,
        "examples": final_examples,
        "questions": raw_questions,
    }


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
    ctx: Any,
    contexts: List[FileAnalysisContext],
) -> AsyncGenerator[bytes, None]:
    client = ctx.client
    orchestrator = ctx.orchestrator
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

    # 상수 컨텍스트는 Neo4j CONSTANT 노드 기반 — catalog와 무관. 없으면 빈 문자열 반환.
    # 테이블 샘플은 catalog 미연결 환경이라 이 경로에서는 수집하지 않고 Jinja 조건부 렌더링으로 섹션 생략.
    constants_context = await fetch_constants_context(client)


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

                # catalog 미연결: 테이블 sample 데이터 없이 진행
                table_data = ""

                # 호출된 프로시저 요약 수집 (CALL 관계)
                called_procedure_summaries = await _collect_called_procedure_summaries(
                    info.procedure_name, processor.full_directory, processor.file_name
                )

                # unit_analysis LLM 은 summary 만 생성. Rule/Example/Question 은 proc_root.analysis_map 사용.
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
                else:
                    async def process_chunk(chunk):
                        r = await asyncio.to_thread(
                            _analyze_summary_only, chunk, orchestrator.api_key, orchestrator.locale,
                            variable_declarations=var_decls,
                            constants_context=constants_context,
                            called_procedure_summaries=called_procedure_summaries,
                        )
                        return r if isinstance(r, dict) else {}

                    chunk_results_raw = await _run_chunks_continuous(chunks, process_chunk, label="DBMS_UNIT_SUMMARY")
                    chunk_summaries = [r.get('summary', '') for r in chunk_results_raw if r and r.get('summary')]

                    if not chunk_summaries:
                        final_summary = ""
                    elif len(chunk_summaries) == 1:
                        final_summary = chunk_summaries[0]
                    else:
                        combined = {f"CHUNK_{i+1}": s for i, s in enumerate(chunk_summaries)}
                        r = await asyncio.to_thread(
                            _analyze_summary_only, combined, orchestrator.api_key, orchestrator.locale,
                            variable_declarations=var_decls,
                            constants_context=constants_context,
                            called_procedure_summaries=called_procedure_summaries,
                        )
                        final_summary = r.get('summary') if isinstance(r, dict) else "\n\n".join(chunk_summaries)
                        final_summary = final_summary or "\n\n".join(chunk_summaries)

                if not final_summary:
                    await results_queue.put({"type": "success", "procedure": info.procedure_name, "file": ctx.file_name})
                    return

                # proc_root.analysis_map (Bottom-up 결과) → Python 후처리로 rule_id/statement 부여 + examples 분리
                final_analysis = _build_final_analysis(proc_root)

                # Summary + Example Mapping 쿼리 조립
                queries = _build_procedure_summary_queries(processor, info, final_summary, final_analysis)
                if queries:
                    async for batch in run_graph_queries(client, queries):
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
    ctx: Any,
    contexts: List[FileAnalysisContext],
) -> AsyncGenerator[bytes, None]:
    client = ctx.client
    orchestrator = ctx.orchestrator
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
    total = len(all_table_stores)
    yield emit_phase_event(2, "AI 분석", "in_progress", 85, {"step": "테이블/컬럼 설명 요약", "total": total})

    # 테이블별 요약을 asyncio.as_completed 로 continuous 처리
    # (완료되는 순서대로 Neo4j 반영 + 진행률 yield)
    tasks = [
        asyncio.create_task(_summarize_table(
            table_key,
            {"summaries": list(data["summaries"]), "columns": data["columns"]},
            all_ddl_metadata, dbms_name or "", orchestrator.api_key, orchestrator.locale,
        ))
        for table_key, data in all_table_stores.items()
    ]

    completed = 0
    total_queries = 0
    # Fail-Fast: 테이블 요약 실패는 상위로 전파
    for coro in asyncio.as_completed(tasks):
        completed += 1
        queries = await coro
        if queries:
            total_queries += len(queries)
            yield emit_message(f"   ✓ [{completed}/{total}] 테이블 요약 완료")
            async for batch in run_graph_queries(client, queries):
                yield emit_data(graph=batch)
        yield emit_phase_event(2, "AI 분석", "in_progress", 85 + int(completed / total * 15), {"step": "테이블/컬럼 설명 요약", "done": completed, "total": total})

    for ctx in contexts:
        if ctx.processor and hasattr(ctx.processor, '_table_summary_store'):
            ctx.processor._table_summary_store.clear()
    yield emit_phase_event(2, "AI 분석", "in_progress", 100, {"step": "테이블/컬럼 설명 요약 완료", "tables": total, "queries": total_queries})


# ========== Phase 3 통합 진입점 ==========
async def run_phase_post(
    ctx: Any,
    contexts: List[FileAnalysisContext],
) -> AsyncGenerator[bytes, None]:
    """Phase 3: 후처리 - 프로시저 요약 → 테이블 요약"""
    async for chunk in _run_unit_analysis(ctx, contexts):
        yield chunk
    async for chunk in _run_table_summary(ctx, contexts):
        yield chunk
