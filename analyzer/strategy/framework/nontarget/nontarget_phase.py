"""2-Phase 4-Step 오케스트레이터 (Phase 3)

비타겟 파일 분석 파이프라인:

Phase 1 (병렬):
  Step 1: 청킹 2-Pass (ChunkerAgent — DeepAgent)
  Step 2: 요약 (RuleLoader LLM chain, tiktoken 배치)
  Step 2.5: FileSummary 생성 (시스템)

Phase 2 (병렬):
  Step 3: 링크 (확정적 Cypher + LLM chain 파이프라인)
  Step 4: Description (RuleLoader LLM chain)

Step 1만 DeepAgent 사용, Step 2/3/4는 고정 파이프라인.
Neo4j 커밋만 순차 (cypher_lock).
"""

import asyncio
import json
import os
from typing import Any, AsyncGenerator, List, Tuple

from analyzer.strategy.base_analyzer import AnalysisStats
from client.neo4j_client import Neo4jClient
from analyzer.strategy.framework.nontarget.workflow import (
    RuntimeCtx,
    run_chunker_2pass,
    run_summarizer_batched,
    run_linker,
    run_description,
    build_file_summaries,
    build_table_column_evidence,
)
from analyzer.strategy.framework.nontarget.cypher_templates import (
    commit_blocks,
    commit_summaries,
    commit_tables,
    commit_columns,
    commit_links,
    commit_descriptions,
    commit_block_table_refers_to,
    commit_explicit_fk,
    commit_nontarget_files,
    commit_block_next,
)
from analyzer.strategy.framework.nontarget.quality_gate import (
    quality_gate_phase1_blocks,
    quality_gate_phase1_summaries,
    quality_gate_phase2_links,
)
from analyzer.strategy.framework.nontarget.agents.deepagents_client import DeepAgentsClient
from analyzer.strategy.framework.nontarget.utils.nontarget_logger import log_nontarget
from config.settings import settings
from util.stream_event import emit_data, emit_message


# 병렬 동시성 제한
_MAX_CONCURRENCY = 3


def _build_graph_from_results(
    results: List[dict],
    label: str,
) -> dict:
    """commit 결과를 emit_data용 graph 형식으로 변환"""
    if not results:
        return {}
    return {
        "Nodes": [
            {
                "Node ID": r.get("id", ""),
                "Labels": [label],
                "Properties": {"name": r.get("name", "")},
            }
            for r in results
            if r.get("id")
        ],
        "Relationships": [],
    }


def _discover_nontarget_files(nontarget_dir: str) -> List[str]:
    """analysis/nontarget 디렉토리에서 비타겟 파일 탐색"""
    nontarget_files: List[str] = []
    for root, dirs, files in os.walk(nontarget_dir):
        for f in files:
            nontarget_files.append(os.path.join(root, f))
    return sorted(nontarget_files)


async def run_nontarget_phase(
    analyzer: Any,
    client: Neo4jClient,
    orchestrator: Any,
    stats: AnalysisStats,
) -> AsyncGenerator[bytes, None]:
    """Phase 3: 비타겟 파일 분석 — 2-Phase 4-Step 오케스트레이터"""
    log_nontarget("PHASE", "Phase 3: 비타겟 파일 분석 시작")
    yield emit_message("   비타겟 파일 탐색 중...")

    # 디렉토리 확인
    analysis_dir = orchestrator.dirs.get("analysis", "")
    if not analysis_dir:
        log_nontarget("PHASE", "analysis 디렉토리 설정 없음")
        yield emit_message("   analysis 디렉토리가 설정되지 않았습니다")
        return

    nontarget_dir = os.path.join(analysis_dir, "nontarget")
    if not os.path.isdir(nontarget_dir):
        log_nontarget("PHASE", f"nontarget 디렉토리 없음: {nontarget_dir}")
        yield emit_message("   nontarget 디렉토리가 없습니다")
        return

    files = _discover_nontarget_files(nontarget_dir)
    if not files:
        log_nontarget("PHASE", "비타겟 파일 없음")
        yield emit_message("   분석할 비타겟 파일이 없습니다")
        return

    yield emit_message(f"   비타겟 파일 {len(files)}개 발견")

    # RuntimeCtx
    ctx = RuntimeCtx(
        client=client,
        cypher_lock=analyzer._cypher_lock,
    )

    # DeepAgentsClient (Step 1 Chunker 전용)
    deepagents_client = DeepAgentsClient()

    # 병렬 동시성 제한
    semaphore = asyncio.Semaphore(_MAX_CONCURRENCY)

    async def _limited(coro):
        async with semaphore:
            return await coro

    # ==================================================================
    # Phase 1: 청킹 + 요약
    # ==================================================================
    yield emit_message("   Phase 1: 청킹 + 요약 시작...")

    # --- Step 1: 청킹 2-Pass (파일 단위 병렬) ---
    log_nontarget("PHASE", f"Step 1: 청킹 ({len(files)}개 파일)")
    yield emit_message(f"   Step 1: 청킹 ({len(files)}개 파일, 병렬)")

    chunk_results: List[Tuple[str, List]] = await asyncio.gather(*[
        _limited(run_chunker_2pass(f, ctx, deepagents_client))
        for f in files
    ])

    # 품질 게이트 + Neo4j 커밋
    all_block_nodes: List[dict] = []
    for file_path, blocks_json in chunk_results:
        if not blocks_json:
            continue
        file_name = os.path.basename(file_path)
        passed, warnings = quality_gate_phase1_blocks(blocks_json, file_path)
        if passed:
            created = await commit_blocks(ctx, file_path, blocks_json)
            all_block_nodes.extend(created)
            # 파일 내 블록 순서 NEXT 관계 생성 (start_line 기준)
            await commit_block_next(ctx, file_path)
            yield emit_message(f"     [{file_name}] {len(blocks_json)}개 블록")

    total_blocks = sum(len(b) for _, b in chunk_results)
    yield emit_message(f"     총 {total_blocks}개 블록 생성 완료")

    graph = _build_graph_from_results(all_block_nodes, "NONTARGET_BLOCK")
    if graph:
        yield emit_data(graph=graph)

    # --- Step 2: 요약 (RuleLoader LLM chain, 배치 병렬) ---
    log_nontarget("PHASE", f"Step 2: 요약 ({total_blocks}개 블록)")
    yield emit_message(f"   Step 2: 요약 ({total_blocks}개 블록, 배치)")

    batch_concurrency = settings.concurrency.nontarget_batch_concurrency
    summary_results: List[Tuple[str, List]] = await asyncio.gather(*[
        _limited(run_summarizer_batched(
            fp, blocks, ctx,
            batch_concurrency=batch_concurrency,
        ))
        for fp, blocks in chunk_results
        if blocks
    ])

    # 품질 게이트 + Neo4j 커밋
    all_table_nodes: List[dict] = []
    all_column_nodes: List[dict] = []
    for file_path, summaries_json in summary_results:
        if not summaries_json:
            continue
        file_name = os.path.basename(file_path)
        passed, warnings = quality_gate_phase1_summaries(summaries_json, file_path)
        if passed:
            # fk_inferred를 JSON 문자열로 직렬화하여 Neo4j에 저장
            for s in summaries_json:
                fk_inf = s.get("fk_inferred", [])
                s["fk_inferred_json"] = json.dumps(fk_inf, ensure_ascii=False) if fk_inf else ""

            await commit_summaries(ctx, file_path, summaries_json)

            # Table/Column upsert
            tables, columns = _extract_tables_columns(summaries_json)
            if tables:
                created_tables = await commit_tables(ctx, tables)
                all_table_nodes.extend(created_tables)
            if columns:
                created_columns = await commit_columns(ctx, columns)
                all_column_nodes.extend(created_columns)

            # NONTARGET_BLOCK -> Table REFERS_TO 자동 생성
            refers_to_items = _build_block_table_refers_to(summaries_json)
            if refers_to_items:
                await commit_block_table_refers_to(ctx, refers_to_items)

            # fk_explicit -> FK_TO_TABLE/FK_TO 즉시 확정
            fk_explicit_items = _extract_fk_explicit(summaries_json)
            if fk_explicit_items:
                await commit_explicit_fk(ctx, fk_explicit_items)

            yield emit_message(f"     [{file_name}] {len(summaries_json)}개 요약")

    total_summaries = sum(len(s) for _, s in summary_results)
    yield emit_message(f"     총 {total_summaries}개 요약 완료")

    # Table/Column 노드 스트림 전달
    table_graph = _build_graph_from_results(all_table_nodes, "Table")
    if table_graph:
        yield emit_data(graph=table_graph)
    col_graph = _build_graph_from_results(all_column_nodes, "Column")
    if col_graph:
        yield emit_data(graph=col_graph)

    # --- Step 2.5: FileSummary 생성 ---
    file_summaries = build_file_summaries(chunk_results, summary_results)
    log_nontarget("PHASE", f"Step 2.5: FileSummary {len(file_summaries)}개 생성")

    # --- NONTARGET_FILE 노드 생성 + CONTAINS 관계 + 패키지 연결 ---
    nontarget_file_infos = _build_nontarget_file_infos(files, nontarget_dir)
    if nontarget_file_infos:
        created_files = await commit_nontarget_files(ctx, nontarget_file_infos)
        yield emit_message(f"     {len(nontarget_file_infos)}개 NONTARGET_FILE 노드 생성")

        file_graph = _build_graph_from_results(created_files, "NONTARGET_FILE")
        if file_graph:
            yield emit_data(graph=file_graph)

    # ==================================================================
    # Phase 2: 링크 + Description
    # ==================================================================
    yield emit_message("   Phase 2: 링크 + Description 시작...")

    # --- Step 3: 링크 (확정적 파이프라인, 파일 단위 병렬) ---
    link_files = [fp for fp in files if fp in file_summaries]
    log_nontarget("PHASE", f"Step 3: 링크 ({len(link_files)}개 파일)")
    yield emit_message(f"   Step 3: 링크 ({len(link_files)}개 파일, 병렬)")

    link_results: List[Tuple[str, dict]] = await asyncio.gather(*[
        _limited(run_linker(fp, file_summaries.get(fp, {}), ctx))
        for fp in link_files
    ])

    # 품질 게이트 + Neo4j 커밋
    total_links = 0
    link_file_paths: List[str] = []
    for file_path, links_json in link_results:
        passed, warnings = quality_gate_phase2_links(links_json, file_path)
        if passed:
            counts = await commit_links(ctx, file_path, links_json)
            link_count = sum(counts.values())
            total_links += link_count
            if link_count > 0:
                link_file_paths.append(file_path)

    yield emit_message(f"     {total_links}개 관계 생성 완료")

    if link_file_paths:
        link_graph = await _fetch_link_graph(ctx, link_file_paths)
        if link_graph and (link_graph.get("Nodes") or link_graph.get("Relationships")):
            yield emit_data(graph=link_graph)

    # --- Step 4: Description (RuleLoader LLM chain) ---
    log_nontarget("PHASE", "Step 4: Description (Table-Column)")
    yield emit_message("   Step 4: Description (Table-Column)")

    table_evidence = await build_table_column_evidence(ctx)

    if table_evidence:
        desc_results = await asyncio.gather(*[
            _limited(run_description(tid, ev, file_summaries, ctx))
            for tid, ev in table_evidence.items()
        ])

        desc_items = [
            desc for _, desc in desc_results
            if desc.get("table_description", "").strip() or desc.get("columns")
        ]
        if desc_items:
            counts = await commit_descriptions(ctx, desc_items)

            desc_node_results = [
                {"id": d.get("table_id", ""), "name": d.get("table_name", "")}
                for d in desc_items
                if d.get("table_id")
            ]
            desc_graph = _build_graph_from_results(desc_node_results, "Table")
            if desc_graph:
                yield emit_data(graph=desc_graph)

            tables_cnt = counts.get("tables", 0)
            columns_cnt = counts.get("columns", 0)
            yield emit_message(f"     테이블 {tables_cnt}개, 컬럼 {columns_cnt}개 description 갱신")
        else:
            yield emit_message("     갱신할 description 없음")
    else:
        yield emit_message("     갱신할 엔티티 없음")

    # ==================================================================
    # 완료
    # ==================================================================
    log_nontarget("PHASE", f"Phase 3 완료: {len(files)}개 파일, {total_blocks}개 블록, {total_links}개 관계")
    yield emit_message(
        f"   비타겟 분석 완료: {len(files)}개 파일, "
        f"{total_blocks}개 블록, {total_summaries}개 요약, {total_links}개 관계"
    )


# ============================================================================
# 헬퍼
# ============================================================================

def _extract_tables_columns(
    summaries: List[dict],
) -> Tuple[List[dict], List[dict]]:
    """Summarizer 결과에서 Table/Column 정보 추출"""
    tables: List[dict] = []
    columns: List[dict] = []

    for s in summaries:
        kind = (s.get("kind") or "").lower()
        # 쿼리/DDL 블록만 테이블/컬럼 메타 생성
        if kind not in ("query", "schema"):
            continue

        for t in s.get("tables", []):
            table_name = t.get("name", "")
            if not table_name:
                continue

            # schema/db는 None/빈 문자열 방어 후 기본값 적용
            schema = (t.get("schema") or "public").strip() or "public"
            db = (t.get("db") or "default").strip() or "default"

            tables.append({
                "name": table_name,
                "entity_name": t.get("entity_name", ""),
                "schema": schema,
                "db": db,
                "description": t.get("description", ""),
                "table_type": t.get("table_type", "table"),
                "mapping_type": t.get("mapping_type", ""),
            })

            for c in t.get("columns", []):
                col_name = c.get("name", "")
                if not col_name:
                    continue

                columns.append({
                    "fqn": f"{table_name.lower()}.{col_name.lower()}",
                    "name": col_name,
                    "dtype": c.get("dtype", ""),
                    "description": c.get("description", ""),
                    "nullable": c.get("nullable", True),
                    "pk_constraint": c.get("pk_constraint", False),
                    "table_name": table_name,
                    "schema": schema,
                })

    return tables, columns


def _build_nontarget_file_infos(
    files: List[str],
    nontarget_dir: str,
) -> List[dict]:
    """비타겟 파일 목록에서 NONTARGET_FILE 노드 생성용 정보를 구성"""
    infos: List[dict] = []
    for fp in files:
        file_name = os.path.basename(fp)
        ext = os.path.splitext(fp)[1].lower()
        rel_path = os.path.relpath(fp, nontarget_dir)
        directory = os.path.dirname(rel_path).replace("\\", "/")
        package_name = directory.replace("/", ".") if directory else ""

        infos.append({
            "file_path": fp,
            "file_name": file_name,
            "ext": ext,
            "directory": directory,
            "package_name": package_name,
        })
    return infos


def _build_block_table_refers_to(
    summaries: List[dict],
) -> List[dict]:
    """Summarizer 결과에서 Block -> Table REFERS_TO 매핑을 구성"""
    items: List[dict] = []
    for s in summaries:
        kind = (s.get("kind") or "").lower()
        # 쿼리/DDL 블록만 REFERS_TO 생성
        if kind not in ("query", "schema"):
            continue

        block_name = s.get("block_name", "")
        file_path = s.get("file_path", "")
        if not block_name or not file_path:
            continue

        for t in s.get("tables", []):
            table_name = t.get("name", "")
            if not table_name:
                continue

            # schema 기본값 public 강제
            schema = (t.get("schema") or "public").strip() or "public"

            access_mode = t.get("access_mode", "")
            role_map = {"r": "reads", "w": "writes", "rw": "reads_writes", "def": "defines"}
            role = role_map.get(access_mode, "references")

            items.append({
                "block_name": block_name,
                "file_path": file_path,
                "table_name": table_name,
                "schema": schema,
                "keyword": table_name,
                "role": role,
                "evidence": s.get("summary", "")[:200],
                "source_lines": f"{s.get('start_line', '')}-{s.get('end_line', '')}",
            })
    return items


def _extract_fk_explicit(
    summaries: List[dict],
) -> List[dict]:
    """Summarizer 결과에서 fk_explicit 항목을 수집"""
    items: List[dict] = []
    for s in summaries:
        kind = (s.get("kind") or "").lower()
        # 쿼리/DDL 블록만 FK 확정
        if kind not in ("query", "schema"):
            continue

        for fk in s.get("fk_explicit", []):
            source_table = fk.get("source_table", "")
            target_table = fk.get("target_table", "")
            if not source_table or not target_table:
                continue
            items.append({
                "source_table": source_table,
                "source_column": fk.get("source_column", ""),
                "target_table": target_table,
                "target_column": fk.get("target_column", ""),
                "fk_type": fk.get("fk_type", "explicit"),
                "evidence": fk.get("evidence", ""),
                "confidence": fk.get("confidence", 0.9),
            })
    return items


async def _fetch_link_graph(
    ctx: Any,
    file_paths: List[str],
) -> dict:
    """링크 관계가 생성된 파일의 그래프 데이터를 조회

    REFERS_TO: NONTARGET_BLOCK → Table (시스템 자동 생성)
    FROM/WRITES: METHOD/CONSTRUCTOR/FIELD → Table (Linker LLM 분석)
    """
    if not file_paths or not ctx.client:
        return {"Nodes": [], "Relationships": []}

    query = """
    UNWIND $items AS item
    CALL {
        WITH item
        MATCH (b:NONTARGET_BLOCK {file_path: item.fp})-[r:REFERS_TO]->(t:Table)
        RETURN b AS src, r, t
        UNION
        WITH item
        MATCH (b:NONTARGET_BLOCK {file_path: item.fp})-[:REFERS_TO]->(t:Table)
        MATCH (m)-[r:FROM|WRITES]->(t)
        WHERE (m:METHOD OR m:CONSTRUCTOR OR m:FIELD)
        RETURN m AS src, r, t
    }
    RETURN src, r, t
    """
    items = [{"fp": fp} for fp in file_paths]
    try:
        if ctx.cypher_lock:
            async with ctx.cypher_lock:
                result = await ctx.client.run_batch_unwind(query, items)
        else:
            result = await ctx.client.run_batch_unwind(query, items)
        return result
    except Exception as e:
        log_nontarget("STREAM", f"링크 그래프 조회 실패 (비치명적): {e}")
        return {"Nodes": [], "Relationships": []}
