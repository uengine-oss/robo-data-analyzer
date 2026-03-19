"""DBMS 파이프라인 페이즈 통합

각 phase 함수를 하나의 파일에 통합합니다.
Phase 0 (DDL), Phase 1 (AST), Phase 2 (LLM), Phase 4 (Vector), Phase 5 (Lineage)
"""

import asyncio
import logging
import os
import time
from typing import Any, AsyncGenerator, List

import aiofiles

from client.neo4j_client import Neo4jClient
from analyzer.strategy.base_analyzer import AnalysisStats
from analyzer.strategy.dbms.common.models import FileStatus, FileAnalysisContext
from analyzer.strategy.dbms.parsing.ast_processor import DbmsAstProcessor
from analyzer.strategy.dbms.common.utils import resolve_default_schema
from analyzer.common.ddl_utils import list_ddl_files
from analyzer.common.ddl_processor import process_ddl
from analyzer.strategy.dbms.linking.lineage_analyzer import LineageAnalyzer, LineageInfo
from client.embedding_client import EmbeddingClient
from config.settings import settings
from util.ndjson_stream import (
    emit_data,
    emit_message,
    emit_phase_event,
)
from util.text_utils import log_process


# ========== Phase 0: DDL ==========
async def run_phase_ddl(
    pipeline: Any,
    client: Neo4jClient,
    orchestrator: Any,
    stats: AnalysisStats,
) -> AsyncGenerator[bytes, None]:
    """Phase 0: DDL 처리 - 테이블/컬럼/스키마 노드 생성

    Args:
        pipeline: DbmsPipeline 인스턴스 (총괄 파이프라인)
        client: Neo4j 클라이언트
        orchestrator: 오케스트레이터
        stats: 분석 통계
    """
    ddl_files = list_ddl_files(orchestrator)
    if not ddl_files:
        yield pipeline.emit_skip("DDL 파일 없음 → 스키마 처리 건너뜀")
        return

    ddl_count = len(ddl_files)
    yield emit_message("")
    yield pipeline.emit_separator()
    yield pipeline.emit_phase_header(0, "📋 DDL 스키마 수집", f"{ddl_count}개 DDL")
    yield pipeline.emit_separator()
    ddl_dir = orchestrator.dirs["ddl"]

    for idx, ddl_file in enumerate(ddl_files, 1):
        yield emit_message("")
        yield pipeline.emit_file_start(idx, ddl_count, ddl_file)
        file_base_progress = int(((idx - 1) / ddl_count) * 100)
        file_end_progress = int((idx / ddl_count) * 100)
        ds_name = getattr(orchestrator, 'datasource_name', '') or ''

        async for chunk in process_ddl(
            ddl_path=os.path.join(ddl_dir, ddl_file),
            client=client,
            file_name=ddl_file,
            orchestrator=orchestrator,
            cypher_lock=pipeline._cypher_lock,
            ddl_schemas=pipeline._ddl_schemas,
            ddl_table_metadata=pipeline._ddl_table_metadata,
            emit_progress=True,
            file_base_progress=file_base_progress,
            file_end_progress=file_end_progress,
            datasource_name=ds_name,
        ):
            if isinstance(chunk, bytes):
                yield chunk
        yield emit_phase_event(0, "DDL 처리", "running", file_end_progress)

    yield emit_message("")
    yield emit_message("📊 DDL 처리 완료:")
    yield emit_message(f"   • 테이블: {stats.ddl_tables}개")
    yield emit_message(f"   • 컬럼: {stats.ddl_columns}개")
    yield emit_message(f"   • FK: {stats.ddl_fks}개")


# ========== Phase 1: AST ==========
async def run_phase_ast(
    pipeline: Any,
    contexts: List[FileAnalysisContext],
    client: Neo4jClient,
    orchestrator: Any,
    stats: AnalysisStats,
) -> AsyncGenerator[bytes, None]:
    """Phase 1: AST 정적 그래프 생성 - 모든 파일 병렬 처리

    Args:
        pipeline: DbmsPipeline 인스턴스 (총괄 파이프라인)
        contexts: 파일 컨텍스트 리스트
        client: Neo4j 클라이언트
        orchestrator: 오케스트레이터
        stats: 분석 통계
    """
    completed = 0
    total = len(contexts)
    results_queue: asyncio.Queue = asyncio.Queue()

    async def process_file(ctx: FileAnalysisContext):
        async with pipeline._file_semaphore:
            try:
                name_case = getattr(orchestrator, 'name_case', 'original')
                default_schema = resolve_default_schema(
                    ctx.directory,
                    pipeline._ddl_schemas,
                    name_case
                )
                last_line = 0
                for child in ctx.ast_data.get('children', []):
                    end = child.get('endLine', 0)
                    if end > last_line:
                        last_line = end

                processor = DbmsAstProcessor(
                    antlr_data=ctx.ast_data,
                    directory=ctx.directory,
                    file_name=ctx.file_name,
                    api_key=orchestrator.api_key,
                    locale=orchestrator.locale,
                    dbms=orchestrator.target,
                    last_line=last_line,
                    default_schema=default_schema,
                    ddl_table_metadata=pipeline._ddl_table_metadata,
                    name_case=name_case,
                    source_content=ctx.source_content,
                )
                ctx.processor = processor
                queries = processor.build_static_graph_queries()

                if queries:
                    all_nodes = {}
                    all_relationships = {}
                    async with pipeline._cypher_lock:
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
                await results_queue.put({"type": "error", "file": ctx.file_name, "message": str(e)})
                raise

    tasks = [asyncio.create_task(process_file(ctx)) for ctx in contexts]

    while completed < total:
        result = await asyncio.wait_for(results_queue.get(), timeout=300.0)
        result_type = result.get("type", "")
        completed += 1
        stats.files_completed = completed
        phase1_progress = int(completed / total * 50)

        if result_type == "error":
            yield emit_message(f"   ❌ [{completed}/{total}] {result['file']}: {result['message'][:50]}")
            stats.mark_file_failed(result['file'], "Phase1 실패")
            yield emit_phase_event(phase_num=1, phase_name="AST 구조 분석", status="in_progress", progress=phase1_progress, details={"file": result['file'], "status": "failed", "completed": completed, "total": total})
        else:
            stats.add_graph_result(result["graph"], is_static=True)
            graph = result["graph"]
            node_count = result.get("node_count", 0)
            rel_count = result.get("rel_count", 0)
            node_types = {}
            for node in graph.get("Nodes", []):
                for label in node.get("Labels", []):
                    node_types[label] = node_types.get(label, 0) + 1

            yield emit_message(f"   ✓ [{completed}/{total}] {result['file']}")
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
                    yield emit_message(f"      → {', '.join(detail_parts)}")
                if rel_count > 0:
                    yield emit_message(f"      → 관계 {rel_count}개 생성 (FROM, WRITES, CALLS 등)")

            yield emit_phase_event(phase_num=1, phase_name="AST 구조 분석", status="in_progress", progress=phase1_progress, details={"file": result['file'], "nodes": node_count, "relationships": rel_count, "completed": completed, "total": total, "node_types": node_types})
            yield emit_data(graph=graph, line_number=0, analysis_progress=int(completed / total * 50), current_file=result["file"])

    await asyncio.gather(*tasks, return_exceptions=True)


# ========== Phase 2: LLM ==========
async def run_phase_llm(
    pipeline: Any,
    contexts: List[FileAnalysisContext],
    client: Neo4jClient,
    orchestrator: Any,
    stats: AnalysisStats,
) -> AsyncGenerator[bytes, None]:
    """Phase 2: LLM 배치 분석 - Phase 1 성공 파일 병렬 처리

    ctx.processor.run_llm_analysis() 호출 후, 프로시저 요약/User Story/테이블 요약 후처리.

    Args:
        pipeline: DbmsPipeline 인스턴스 (총괄 파이프라인)
        contexts: Phase 1 성공 파일 컨텍스트 리스트
        client: Neo4j 클라이언트
        orchestrator: 오케스트레이터
        stats: 분석 통계
    """
    if not contexts:
        yield emit_message("   ℹ️ 분석 대상 파일 없음")
        return

    completed = 0
    total = len(contexts)
    results_queue: asyncio.Queue = asyncio.Queue()
    max_workers = pipeline._file_semaphore._value if hasattr(pipeline._file_semaphore, '_value') else 5

    yield emit_message("")
    yield emit_message(f"┌─────────────────────────────────────────────────────────┐")
    yield emit_message(f"│  📊 Phase 2: AI 분석 시작                                │")
    yield emit_message(f"│     총 {total}개 파일, 동시 처리: {max_workers}개                         │")
    yield emit_message(f"└─────────────────────────────────────────────────────────┘")

    async def analyze_file(ctx: FileAnalysisContext):
        async with pipeline._file_semaphore:
            try:
                if not ctx.processor:
                    raise RuntimeError(f"Phase 1에서 프로세서 초기화 실패: {ctx.file_name}")
                all_nodes = {}
                all_relationships = {}

                async def on_queries_ready(queries, batch_id, total_batches):
                    if not queries:
                        return
                    async with pipeline._cypher_lock:
                        async for batch_result in client.run_graph_query(queries):
                            for node in batch_result.get("Nodes", []):
                                all_nodes[node["Node ID"]] = node
                            for rel in batch_result.get("Relationships", []):
                                all_relationships[rel["Relationship ID"]] = rel
                            await results_queue.put({
                                "type": "batch_progress",
                                "file": ctx.file_name,
                                "batch_id": batch_id,
                                "total_batches": total_batches,
                                "graph": {
                                    "Nodes": batch_result.get("Nodes", []),
                                    "Relationships": batch_result.get("Relationships", []),
                                },
                            })

                analysis_queries, failed_batch_count, failed_details, unit_summary_store = await ctx.processor.run_llm_analysis(
                    on_queries_ready=on_queries_ready,
                )
                ctx.unit_summary_store = unit_summary_store
                graph = {"Nodes": list(all_nodes.values()), "Relationships": list(all_relationships.values())}
                ctx.status = FileStatus.PH2_OK
                await results_queue.put({
                    "type": "success",
                    "file": ctx.file_name,
                    "graph": graph,
                    "query_count": len(analysis_queries),
                    "failed_batches": failed_batch_count,
                    "failed_details": failed_details,
                })
                if failed_batch_count > 0:
                    raise RuntimeError(f"{ctx.file_name}: {failed_batch_count}개 배치 실패")
            except Exception as e:
                log_process("ANALYZE", "ERROR", f"Phase 2 오류 ({ctx.file_name}): {e}", logging.ERROR, e)
                ctx.status = FileStatus.PH2_FAIL
                ctx.error_message = str(e)[:100]
                await results_queue.put({"type": "error", "file": ctx.file_name, "message": str(e)})
                raise

    tasks = [asyncio.create_task(analyze_file(ctx)) for ctx in contexts]

    while completed < total:
        result = await asyncio.wait_for(results_queue.get(), timeout=600.0)
        result_type = result.get("type", "")

        if result_type == "warning":
            yield emit_message(f"   ⚠️ {result['file']}: {result['message']}")
            continue

        if result_type == "batch_progress":
            batch_id = result.get("batch_id", 0)
            total_batches = result.get("total_batches", 0)
            graph = result.get("graph")
            node_count = len(graph.get("Nodes", [])) if graph else 0
            batch_label = f"#{batch_id}/{total_batches}" if total_batches > 0 else f"#{batch_id}"
            log_process("STREAM", "BATCH", f"{result.get('file')}: 스트림 전송 완료 (배치 {batch_label}, 노드 {node_count}개)")
            yield emit_message(f"│     📦 배치 {batch_label} 저장 완료 (노드 {node_count}개)")
            if graph:
                yield emit_data(graph=graph, current_file=result.get("file"))
            continue

        completed += 1
        remaining = total - completed
        phase2_progress = int(completed / total * 100)

        if result_type == "error":
            yield emit_message(f"├─ ❌ [{completed}/{total}] {result['file']}")
            yield emit_message(f"│     오류: {result['message'][:80]}")
            yield emit_message(f"│     남은 파일: {remaining}개")
            stats.mark_file_failed(result['file'], "Phase2 실패")
            yield emit_phase_event(phase_num=2, phase_name="AI 분석", status="in_progress", progress=phase2_progress, details={"file": result['file'], "status": "failed", "completed": completed, "total": total})
        else:
            stats.llm_batches_executed += 1
            graph = result["graph"]
            stats.add_graph_result(graph, is_static=False)
            failed_batches = result.get("failed_batches", 0)
            failed_details = result.get("failed_details", [])
            node_count = len(graph.get("Nodes", []))
            rel_count = len(graph.get("Relationships", []))
            status_icon = "✅" if failed_batches == 0 else "⚠️"

            yield emit_message(f"├─ {status_icon} [{completed}/{total}] {result['file']}")
            yield emit_message(f"│     쿼리: {result['query_count']}개, 노드: {node_count}개, 관계: {rel_count}개")
            if remaining > 0:
                yield emit_message(f"│     남은 파일: {remaining}개")

            summary_added = sum(1 for n in graph.get("Nodes", []) if n.get("Properties", {}).get("summary"))
            table_desc_added = sum(1 for n in graph.get("Nodes", []) if "Table" in (n.get("Labels") or []) and n.get("Properties", {}).get("analyzed_description"))
            if summary_added or table_desc_added:
                detail_parts = []
                if summary_added:
                    detail_parts.append(f"요약 {summary_added}개")
                if table_desc_added:
                    detail_parts.append(f"테이블 설명 {table_desc_added}개")
                yield emit_message(f"│     생성: {', '.join(detail_parts)}")

            if failed_details:
                stats.llm_batches_failed += len(failed_details)
                for detail in failed_details[:3]:
                    yield emit_message(f"│     ⚠️ 배치 #{detail['batch_id']}: {detail['error'][:50]}")

            yield emit_phase_event(phase_num=2, phase_name="AI 분석", status="in_progress", progress=phase2_progress, details={"file": result['file'], "queries": result['query_count'], "nodes_updated": node_count, "relationships_updated": rel_count, "completed": completed, "total": total})
            yield emit_data(analysis_progress=phase2_progress, current_file=result["file"])

    await asyncio.gather(*tasks, return_exceptions=True)

    from analyzer.strategy.dbms.linking.summarizer import run_phase_post
    async for chunk in run_phase_post(pipeline, contexts, client, orchestrator, stats):
        yield chunk

    yield emit_phase_event(2, "AI 분석", "completed", 100, {"batches": stats.llm_batches_executed})


# ========== Phase: Vector ==========
async def run_phase_vector(
    pipeline: Any,
    client: Neo4jClient,
    orchestrator: Any,
    stats: AnalysisStats,
    phase_num: int = 4,
) -> AsyncGenerator[bytes, None]:
    """테이블/컬럼 벡터라이징 (배치 최적화)

    Neo4j에 저장된 테이블/컬럼의 description을 기반으로 임베딩 생성
    배치 처리로 성능 최적화

    Args:
        pipeline: DbmsPipeline 인스턴스 (총괄 파이프라인)
        client: Neo4j 클라이언트
        orchestrator: 오케스트레이터
        stats: 분석 통계
        phase_num: 프론트엔드에 전달할 phase 번호 (기본 4)
    """
    from openai import AsyncOpenAI

    # OpenAI 클라이언트 초기화
    api_key = orchestrator.api_key or settings.openai_api_key
    if not api_key:
        yield emit_message("   ⚠️ OpenAI API 키가 없어 벡터라이징을 건너뜁니다")
        return

    openai_client = AsyncOpenAI(api_key=api_key)
    embedding_client = EmbeddingClient(openai_client)

    # ===========================================
    # 테이블 벡터라이징 (배치 처리)
    # ===========================================
    yield emit_message(f"   📊 [Phase {phase_num}-1] 테이블 벡터라이징 시작...")
    yield emit_phase_event(
        phase_num=phase_num,
        phase_name="벡터라이징",
        status="in_progress",
        progress=0,
        details={"step": "테이블 벡터라이징"}
    )

    # description이 없어도 테이블명으로 임베딩 생성 (fallback 지원)
    table_query = """
    MATCH (__cy_t__:Table)
    WHERE (__cy_t__.vector IS NULL OR size(__cy_t__.vector) = 0)
    RETURN elementId(__cy_t__) AS tid,
           __cy_t__.name AS name,
           __cy_t__.schema AS schema,
           CASE
             WHEN __cy_t__.description IS NOT NULL AND __cy_t__.description <> ''
               THEN __cy_t__.description +
                    CASE WHEN __cy_t__.analyzed_description IS NOT NULL AND __cy_t__.analyzed_description <> ''
                         THEN ' | AI 분석: ' + __cy_t__.analyzed_description
                         ELSE ''
                    END
             WHEN __cy_t__.analyzed_description IS NOT NULL AND __cy_t__.analyzed_description <> ''
               THEN __cy_t__.analyzed_description
             ELSE NULL
           END AS description
    ORDER BY __cy_t__.schema, __cy_t__.name
    """

    try:
        async with pipeline._cypher_lock:
            result = await client.execute_queries([table_query])

        tables = result[0] if result and result[0] else []
        total_tables = len(tables)

        if total_tables == 0:
            yield emit_message("      ℹ️ 벡터화할 테이블이 없습니다")
        else:
            yield emit_message(f"      📋 벡터화 대상: {total_tables}개 테이블")

            # 테이블도 배치로 처리 (50개씩)
            batch_size = 50
            for batch_idx in range(0, total_tables, batch_size):
                batch = tables[batch_idx:batch_idx + batch_size]
                batch_num = batch_idx // batch_size + 1
                total_batches = (total_tables + batch_size - 1) // batch_size

                # 유효한 테이블만 필터링 (description이 없으면 테이블명 사용)
                valid_items = []
                texts = []
                for item in batch:
                    table_name = item.get("name", "")
                    schema_name = item.get("schema", "")
                    description = item.get("description", "") or ""

                    # description이 없으면 테이블명과 스키마로 임베딩 텍스트 생성
                    if description:
                        text = embedding_client.format_table_text(
                            table_name=table_name,
                            description=description
                        )
                    else:
                        # fallback: 테이블명과 스키마명으로 임베딩
                        text = f"테이블: {schema_name}.{table_name}" if schema_name else f"테이블: {table_name}"

                    texts.append(text)
                    valid_items.append(item)

                if not texts:
                    continue

                # 배치 진행 상황 표시
                batch_progress = int(batch_idx / total_tables * 25)  # 0-25% 범위
                log_process("VECTORIZE", "TABLE", f"배치 #{batch_num}/{total_batches} 테이블 {len(valid_items)}개 임베딩 생성 시작", logging.INFO)
                yield emit_message(f"      🔄 [{batch_num}/{total_batches}] 테이블 {len(valid_items)}개 임베딩 생성 중...")
                yield emit_phase_event(
                    phase_num=phase_num,
                    phase_name="벡터라이징",
                    status="in_progress",
                    progress=batch_progress,
                    details={"step": "테이블 임베딩", "done": batch_num, "total": total_batches}
                )

                # 배치 임베딩 API 호출 (시간 측정)
                embed_start = time.time()
                vectors = await embedding_client.embed_batch(texts)
                embed_time = time.time() - embed_start
                log_process("VECTORIZE", "API", f"임베딩 API 응답: {len(vectors)}개, {embed_time:.2f}초", logging.INFO)

                # UNWIND 배치 저장용 데이터 생성
                vector_updates = []
                for item, vector in zip(valid_items, vectors):
                    if vector:
                        vector_updates.append({
                            "tid": item['tid'],
                            "vector": vector
                        })
                        stats.tables_vectorized += 1

                # UNWIND로 한번에 저장
                if vector_updates:
                    update_query = """
                    UNWIND $items AS item
                    MATCH (__cy_t__) WHERE elementId(__cy_t__) = item.tid
                    SET __cy_t__.vector = item.vector
                    RETURN __cy_t__
                    """
                    async with pipeline._cypher_lock:
                        await client.execute_with_params(update_query, {"items": vector_updates})

                    yield emit_message(f"      ✓ [{batch_num}/{total_batches}] {len(vector_updates)}개 테이블 벡터 저장 완료")

            yield emit_message(f"   ✅ 테이블 벡터라이징 완료: {stats.tables_vectorized}개 테이블")

    except Exception as e:
        error_msg = f"테이블 벡터라이징 실패: {str(e)}"
        yield emit_message(f"   ❌ {error_msg}")
        raise RuntimeError(error_msg) from e

    # ===========================================
    # 컬럼 벡터라이징 (배치 처리)
    # ===========================================
    yield emit_message(f"   📊 [Phase {phase_num}-2] 컬럼 벡터라이징 시작...")
    yield emit_phase_event(
        phase_num=phase_num,
        phase_name="벡터라이징",
        status="in_progress",
        progress=25,
        details={"step": "컬럼 벡터라이징"}
    )

    # description과 analyzed_description을 합쳐서 임베딩 생성 (검색 품질 향상)
    column_query = """
    MATCH (__cy_t__:Table)-[:HAS_COLUMN]->(__cy_c__:Column)
    WHERE (__cy_c__.vector IS NULL OR size(__cy_c__.vector) = 0)
      AND (__cy_c__.description IS NOT NULL OR __cy_c__.analyzed_description IS NOT NULL)
    RETURN elementId(__cy_c__) AS cid,
           __cy_c__.name AS column_name,
           __cy_t__.name AS table_name,
           coalesce(__cy_c__.dtype, '') AS dtype,
           trim(
             coalesce(__cy_c__.description, '') +
             CASE WHEN __cy_c__.analyzed_description IS NOT NULL AND __cy_c__.analyzed_description <> ''
                  THEN ' | AI 분석: ' + __cy_c__.analyzed_description
                  ELSE ''
             END
           ) AS description
    ORDER BY __cy_t__.schema, __cy_t__.name, __cy_c__.name
    """

    try:
        async with pipeline._cypher_lock:
            result = await client.execute_queries([column_query])

        columns = result[0] if result and result[0] else []
        total_columns = len(columns)

        if total_columns == 0:
            yield emit_message("      ℹ️ 벡터화할 컬럼이 없습니다")
        else:
            yield emit_message(f"      📋 벡터화 대상: {total_columns}개 컬럼")

            # 배치 처리 (50개씩)
            batch_size = 50
            for i in range(0, total_columns, batch_size):
                batch = columns[i:i + batch_size]
                batch_num = i // batch_size + 1
                total_batches = (total_columns + batch_size - 1) // batch_size
                texts = []

                for item in batch:
                    text = embedding_client.format_column_text(
                        column_name=item.get("column_name", ""),
                        table_name=item.get("table_name", ""),
                        dtype=item.get("dtype", ""),
                        description=item.get("description", "")
                    )
                    texts.append(text)

                # 배치 진행 상황 표시
                batch_progress = 25 + int(i / total_columns * 75)  # 25-100% 범위
                log_process("VECTORIZE", "COLUMN", f"배치 #{batch_num}/{total_batches} 컬럼 {len(texts)}개 임베딩 생성 시작", logging.INFO)
                yield emit_message(f"      🔄 [{batch_num}/{total_batches}] 컬럼 {len(texts)}개 임베딩 생성 중...")
                yield emit_phase_event(
                    phase_num=phase_num,
                    phase_name="벡터라이징",
                    status="in_progress",
                    progress=batch_progress,
                    details={"step": "컬럼 임베딩", "done": batch_num, "total": total_batches}
                )

                # 배치 임베딩 API 호출 (시간 측정)
                embed_start = time.time()
                vectors = await embedding_client.embed_batch(texts)
                embed_time = time.time() - embed_start
                log_process("VECTORIZE", "API", f"임베딩 API 응답: {len(vectors)}개, {embed_time:.2f}초", logging.INFO)

                # UNWIND 배치 저장용 데이터 생성
                vector_updates = []
                for item, vector in zip(batch, vectors):
                    if vector:
                        vector_updates.append({
                            "cid": item['cid'],
                            "vector": vector
                        })
                        stats.columns_vectorized += 1

                # UNWIND로 한번에 저장
                if vector_updates:
                    update_query = """
                    UNWIND $items AS item
                    MATCH (__cy_c__) WHERE elementId(__cy_c__) = item.cid
                    SET __cy_c__.vector = item.vector
                    RETURN __cy_c__
                    """
                    async with pipeline._cypher_lock:
                        await client.execute_with_params(update_query, {"items": vector_updates})

                    yield emit_message(f"      ✓ [{batch_num}/{total_batches}] {len(vector_updates)}개 컬럼 벡터 저장 완료")

            yield emit_message(f"   ✅ 컬럼 벡터라이징 완료: {stats.columns_vectorized}개 컬럼")
            yield emit_phase_event(
                phase_num=phase_num,
                phase_name="벡터라이징",
                status="completed",
                progress=100,
                details={"tables_vectorized": stats.tables_vectorized, "columns_vectorized": stats.columns_vectorized}
            )

    except Exception as e:
        error_msg = f"컬럼 벡터라이징 실패: {str(e)}"
        yield emit_message(f"   ❌ {error_msg}")
        raise RuntimeError(error_msg) from e


# ========== Phase: Lineage ==========
async def run_phase_lineage(
    pipeline: Any,
    client: Neo4jClient,
    orchestrator: Any,
    stats: AnalysisStats,
    phase_num: int = 5,
) -> AsyncGenerator[bytes, None]:
    """ETL 패턴 감지 및 데이터 리니지 관계 생성

    Stored Procedure가 ETL 역할을 하는지 분석하고,
    Source 테이블 → ETL → Target 테이블 간 데이터 흐름 관계를 생성합니다.

    Args:
        pipeline: DbmsPipeline 인스턴스 (총괄 파이프라인)
        client: Neo4j 클라이언트
        orchestrator: 오케스트레이터
        stats: 분석 통계
        phase_num: 프론트엔드에 전달할 phase 번호 (기본 5)
    """
    source_dir = orchestrator.dirs.get("source", "")

    if not source_dir or not os.path.exists(source_dir):
        yield emit_message("   ℹ️ SP 파일 없음 → 리니지 분석 건너뜀")
        return

    # SP 파일 목록 가져오기
    sql_files: List[str] = []
    for root, _, files in os.walk(source_dir):
        for f in files:
            if f.endswith(".sql"):
                sql_files.append(os.path.join(root, f))

    if not sql_files:
        yield emit_message("   ℹ️ SP 파일 없음 → 리니지 분석 건너뜀")
        return

    total_files = len(sql_files)
    log_process("LINEAGE", "START", f"리니지 분석 시작: {total_files}개 SP 파일", logging.INFO)
    yield emit_message(f"   🔍 [Phase {phase_num}] {total_files}개 SP 파일에서 ETL 패턴 분석 시작...")
    yield emit_phase_event(
        phase_num=phase_num,
        phase_name="리니지 분석",
        status="in_progress",
        progress=0,
        details={"total_files": total_files}
    )

    # 리니지 분석기 생성 (orchestrator.target 사용, 기본값: oracle)
    dbms = getattr(orchestrator, "target", "oracle") or "oracle"
    lineage_analyzer = LineageAnalyzer(dbms=dbms)
    all_lineages: List[LineageInfo] = []
    files_with_etl = 0

    # 각 SP 파일 분석
    for idx, sql_file in enumerate(sql_files, 1):
        file_name = os.path.basename(sql_file)
        progress = int((idx / total_files) * 80)  # 0-80% 범위 (저장은 80-100%)

        log_process("LINEAGE", "ANALYZE", f"[{idx}/{total_files}] {file_name} 분석 중", logging.INFO)

        try:
            async with aiofiles.open(sql_file, "r", encoding="utf-8", errors="ignore") as f:
                sql_content = await f.read()

            # 리니지 분석
            lineages = lineage_analyzer.analyze_sql_content(sql_content, file_name)

            # ETL 패턴이 감지된 경우만 저장
            etl_lineages = [l for l in lineages if l.is_etl]
            if etl_lineages:
                files_with_etl += 1
                for l in etl_lineages:
                    l.file_name = file_name
                all_lineages.extend(etl_lineages)

                # 상세 정보 로깅: 소스/타겟 테이블 표시
                source_tables = set()
                target_tables = set()
                for l in etl_lineages:
                    source_tables.update(l.source_tables or [])
                    target_tables.update(l.target_tables or [])

                log_process("LINEAGE", "ETL_FOUND",
                    f"{file_name}: ETL {len(etl_lineages)}개 (소스: {len(source_tables)}개, 타겟: {len(target_tables)}개)",
                    logging.INFO)
                yield emit_message(
                    f"      ✅ [{idx}/{total_files}] {file_name}: ETL {len(etl_lineages)}개 감지"
                )
            else:
                yield emit_message(f"      ⏭️ [{idx}/{total_files}] {file_name}: ETL 패턴 없음")

            yield emit_phase_event(
                phase_num=phase_num,
                phase_name="리니지 분석",
                status="in_progress",
                progress=progress,
                details={"current_file": file_name, "done": idx, "total": total_files, "etl_found": len(all_lineages)}
            )

        except Exception as e:
            error_msg = f"{file_name} 리니지 분석 실패: {e}"
            log_process("LINEAGE", "ERROR", error_msg, logging.ERROR, e)
            raise RuntimeError(error_msg) from e

    log_process("LINEAGE", "SCAN_DONE", f"파일 스캔 완료: {files_with_etl}/{total_files}개 파일에서 ETL 패턴 발견", logging.INFO)

    # ETL 패턴이 감지된 경우 Neo4j에 저장
    if all_lineages:
        log_process("LINEAGE", "SAVE_START", f"Neo4j 저장 시작: {len(all_lineages)}개 ETL 패턴", logging.INFO)
        yield emit_message(f"\n   💾 총 {len(all_lineages)}개 ETL 패턴 → Neo4j 저장 중...")
        yield emit_phase_event(
            phase_num=phase_num,
            phase_name="리니지 분석",
            status="in_progress",
            progress=85,
            details={"step": "Neo4j 저장", "total": len(all_lineages)}
        )

        try:
            # name_case 옵션 가져오기
            name_case = getattr(orchestrator, "name_case", "original")

            result = await lineage_analyzer.save_lineage_to_neo4j(
                client=client,
                lineage_list=all_lineages,
                file_name="",
                name_case=name_case,
            )

            # 통계 업데이트
            if not hasattr(stats, 'etl_count'):
                stats.etl_count = 0
            if not hasattr(stats, 'data_flows'):
                stats.data_flows = 0

            stats.etl_count = result.get("etl_nodes", 0)
            stats.data_flows = result.get("data_flows", 0)

            log_process("LINEAGE", "COMPLETE",
                f"리니지 저장 완료: ETL {result.get('etl_nodes', 0)}개, "
                f"READS {result.get('etl_reads', 0)}개, WRITES {result.get('etl_writes', 0)}개, "
                f"DATA_FLOWS {result.get('data_flows', 0)}개",
                logging.INFO)

            yield emit_message(
                f"   ✅ 리니지 저장 완료: "
                f"ETL 프로시저 {result.get('etl_nodes', 0)}개, "
                f"ETL_READS {result.get('etl_reads', 0)}개, "
                f"ETL_WRITES {result.get('etl_writes', 0)}개, "
                f"DATA_FLOWS_TO {result.get('data_flows', 0)}개"
            )
            yield emit_phase_event(
                phase_num=phase_num,
                phase_name="리니지 분석",
                status="completed",
                progress=100,
                details={
                    "etl_nodes": result.get('etl_nodes', 0),
                    "etl_reads": result.get('etl_reads', 0),
                    "etl_writes": result.get('etl_writes', 0),
                    "data_flows": result.get('data_flows', 0)
                }
            )

        except Exception as e:
            error_msg = f"리니지 저장 실패: {str(e)}"
            yield emit_message(f"   ❌ {error_msg}")
            log_process("LINEAGE", "ERROR", error_msg, logging.ERROR, e)
            raise RuntimeError(error_msg) from e
    else:
        log_process("LINEAGE", "SKIP", "ETL 패턴 없음 - 리니지 관계 미생성", logging.INFO)
        yield emit_message("   ℹ️ ETL 패턴 없음 → 리니지 관계 미생성")
        yield emit_phase_event(
            phase_num=phase_num,
            phase_name="리니지 분석",
            status="completed",
            progress=100,
            details={"etl_nodes": 0, "message": "no_etl_patterns"}
        )
