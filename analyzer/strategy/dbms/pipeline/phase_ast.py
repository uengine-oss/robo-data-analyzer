"""DBMS Phase 1: AST 정적 그래프 생성 — 모든 파일 병렬 처리."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, AsyncGenerator, List

from analyzer.strategy.dbms.common.models.node_models import FileStatus, FileAnalysisContext
from analyzer.strategy.dbms.analysis.ast_analyzer import DbmsAstProcessor
from analyzer.strategy.dbms.common.domain.naming_helpers import resolve_default_schema
from analyzer.common.cypher.graph_writer import run_graph_queries_collect
from analyzer.common.cypher.schema_resolver import extract_own_schema_from_ast
from util.stream_emitter import emit_data, emit_user_message, emit_phase_event
from util.logger import log_process

logger = logging.getLogger(__name__)


async def run_phase_ast(
    ctx: Any,  # DbmsPhaseContext
    contexts: List[FileAnalysisContext],
) -> AsyncGenerator[bytes, None]:
    """Phase 1: AST 정적 그래프 생성."""
    client = ctx.client
    stats = ctx.stats
    completed = 0
    total = len(contexts)
    results_queue: asyncio.Queue = asyncio.Queue()

    async def process_file(file_ctx: FileAnalysisContext):
        async with ctx.file_semaphore:
            try:
                # own_schema 우선순위: AST 의 PROCEDURE schema → 폴더 기반 (DDL 매칭 / 폴더명).
                # AST 도 폴더도 못 잡으면 빈값 — schema_resolver 가 위계 다음 단계 (DDL 매칭) 로 떨어뜨림.
                own_schema = extract_own_schema_from_ast(file_ctx.ast_data)
                if not own_schema:
                    own_schema = resolve_default_schema(
                        file_ctx.directory, ctx.ddl_schemas, ctx.name_case,
                    )
                file_ctx.own_schema = own_schema
                last_line = 0
                for child in file_ctx.ast_data.get('children', []):
                    end = child.get('endLine', 0)
                    if end > last_line:
                        last_line = end

                processor = DbmsAstProcessor(
                    antlr_data=file_ctx.ast_data,
                    directory=file_ctx.directory,
                    file_name=file_ctx.file_name,
                    api_key=ctx.api_key,
                    locale=ctx.locale,
                    dbms=ctx.target,
                    last_line=last_line,
                    own_schema=own_schema,
                    name_to_schema=ctx.name_to_schema,
                    ddl_table_metadata=ctx.ddl_table_metadata,
                    ddl_table_index=ctx.ddl_table_index,
                    ddl_pattern=ctx.ddl_pattern,
                    name_case=ctx.name_case,
                    source_content=file_ctx.source_content,
                    datasource_name=ctx.datasource_name,
                )
                file_ctx.processor = processor
                queries = processor.build_static_graph_queries()

                if queries:
                    graph = await run_graph_queries_collect(client, queries)
                    node_count = len(graph.get("Nodes", []))
                    rel_count = len(graph.get("Relationships", []))
                    file_ctx.status = FileStatus.PH1_OK
                    await results_queue.put({
                        "type": "success", "file": file_ctx.file_name, "graph": graph,
                        "node_count": node_count, "rel_count": rel_count,
                    })
                else:
                    file_ctx.status = FileStatus.PH1_OK
                    await results_queue.put({
                        "type": "success", "file": file_ctx.file_name,
                        "graph": {"Nodes": [], "Relationships": []},
                        "node_count": 0, "rel_count": 0,
                    })
            except Exception as e:
                log_process("PHASE:1", "ERROR", f"Phase 1 오류 ({file_ctx.file_name}): {e}", logging.ERROR, e)
                file_ctx.status = FileStatus.PH1_FAIL
                file_ctx.error_message = str(e)[:100]
                await results_queue.put({"type": "error", "file": file_ctx.file_name, "message": str(e)})
                raise

    tasks = [asyncio.create_task(process_file(file_ctx)) for file_ctx in contexts]

    while completed < total:
        result = await asyncio.wait_for(results_queue.get(), timeout=300.0)
        result_type = result.get("type", "")
        completed += 1
        stats.files_completed = completed
        phase1_progress = int(completed / total * 50)

        if result_type == "error":
            yield emit_user_message(f"   ❌ [{completed}/{total}] {result['file']}: {result['message'][:50]}")
            stats.mark_file_failed(result['file'], "Phase1 실패")
            yield emit_phase_event(1, "AST 구조 분석", "in_progress", phase1_progress,
                                   {"file": result['file'], "status": "failed", "completed": completed, "total": total})
        else:
            stats.add_graph_result(result["graph"], is_static=True)
            graph = result["graph"]
            node_count = result.get("node_count", 0)
            rel_count = result.get("rel_count", 0)
            node_types = {}
            for node in graph.get("Nodes", []):
                for label in node.get("Labels", []):
                    node_types[label] = node_types.get(label, 0) + 1

            yield emit_user_message(f"   ✓ [{completed}/{total}] {result['file']}")
            if node_types:
                proc_count = node_types.get("PROCEDURE", 0) + node_types.get("FUNCTION", 0)
                stmt_count = sum(v for k, v in node_types.items() if k in ["SELECT", "INSERT", "UPDATE", "DELETE", "MERGE"])
                table_refs = node_types.get("Table", 0)
                detail_parts = []
                if proc_count:
                    detail_parts.append(f"프로시저/함수 {proc_count}개")
                if stmt_count:
                    detail_parts.append(f"SQL문 {stmt_count}개")
                if table_refs:
                    detail_parts.append(f"테이블 참조 {table_refs}개")
                if detail_parts:
                    yield emit_user_message(f"      → {', '.join(detail_parts)}")
                if rel_count > 0:
                    yield emit_user_message(f"      → 관계 {rel_count}개 생성 (FROM, WRITES, CALLS 등)")

            yield emit_phase_event(1, "AST 구조 분석", "in_progress", phase1_progress,
                                   {"file": result['file'], "nodes": node_count, "relationships": rel_count,
                                    "completed": completed, "total": total, "node_types": node_types})
            yield emit_data(graph=graph, line_number=0, analysis_progress=int(completed / total * 50), current_file=result["file"])

    await asyncio.gather(*tasks, return_exceptions=True)
