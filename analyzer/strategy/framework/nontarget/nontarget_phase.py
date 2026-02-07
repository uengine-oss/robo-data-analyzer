"""2-Phase 4-Step 오케스트레이터 (Phase 3)

비타겟 파일 분석 파이프라인:

Phase 1 (병렬):
  Step 1: 청킹 2-Pass (파일 단위 병렬)
  Step 2: 요약 (파일 단위 병렬 + tiktoken 2000 배치)
  Step 2.5: FileSummary 생성 (시스템)
  벡터화

Phase 2 (병렬):
  Step 3: 링크 (파일 단위 병렬 + 검색 전략 병렬)
  Step 4: Description (엔티티 단위)

에이전트 = 분석만 (JSON 반환), 시스템 = 저장만 (템플릿 Cypher).
Neo4j 커밋만 순차 (cypher_lock).
"""

import asyncio
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
    build_entity_evidence,
    vectorize_all_blocks,
)
from analyzer.strategy.framework.nontarget.cypher_templates import (
    commit_blocks,
    commit_summaries,
    commit_tables,
    commit_columns,
    commit_links,
    commit_descriptions,
)
from analyzer.strategy.framework.nontarget.quality_gate import (
    quality_gate_phase1_blocks,
    quality_gate_phase1_summaries,
    quality_gate_phase2_links,
)
from analyzer.strategy.framework.nontarget.agents.deepagents_client import DeepAgentsClient
from analyzer.strategy.framework.nontarget.tools.search_tool import ensure_vector_indexes
from analyzer.strategy.framework.nontarget.utils.nontarget_logger import log_nontarget
from util.stream_event import emit_message


# 병렬 동시성 제한
_MAX_CONCURRENCY = 3


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
    """Phase 3: 비타겟 파일 분석 — 2-Phase 4-Step 오케스트레이터

    Args:
        analyzer: FrameworkAnalyzer 인스턴스
        client: Neo4j 클라이언트
        orchestrator: 오케스트레이터
        stats: 분석 통계

    Yields:
        스트리밍 이벤트
    """
    log_nontarget("PHASE", "Phase 3: 비타겟 파일 분석 시작 (2-Phase 4-Step)")
    yield emit_message("   🔍 비타겟 파일 탐색 중...")

    # 디렉토리 확인
    analysis_dir = orchestrator.dirs.get("analysis", "")
    if not analysis_dir:
        log_nontarget("PHASE", "analysis 디렉토리 설정 없음")
        yield emit_message("   ℹ️ analysis 디렉토리가 설정되지 않았습니다")
        return

    nontarget_dir = os.path.join(analysis_dir, "nontarget")
    if not os.path.isdir(nontarget_dir):
        log_nontarget("PHASE", f"nontarget 디렉토리 없음: {nontarget_dir}")
        yield emit_message("   ℹ️ nontarget 디렉토리가 없습니다")
        return

    files = _discover_nontarget_files(nontarget_dir)
    if not files:
        log_nontarget("PHASE", "비타겟 파일 없음")
        yield emit_message("   ℹ️ 분석할 비타겟 파일이 없습니다")
        return

    yield emit_message(f"   📋 비타겟 파일 {len(files)}개 발견")

    # mem/ 디렉토리 초기화
    _init_memory_dirs()

    # RuntimeCtx
    ctx = RuntimeCtx(
        client=client,
        cypher_lock=analyzer._cypher_lock,
    )

    # 벡터 인덱스 확인
    try:
        created_indexes = await ensure_vector_indexes(ctx)
        if created_indexes:
            yield emit_message(f"   🗂️ 벡터 인덱스 {len(created_indexes)}개 생성")
    except Exception as e:
        log_nontarget("PHASE", f"벡터 인덱스 확인 실패 (비치명적): {e}")

    # DeepAgentsClient (에이전트 팩토리)
    deepagents_client = DeepAgentsClient()

    # 병렬 동시성 제한
    semaphore = asyncio.Semaphore(_MAX_CONCURRENCY)

    async def _limited(coro):
        async with semaphore:
            return await coro

    # ==================================================================
    # Phase 1: 청킹 + 요약
    # ==================================================================
    yield emit_message("   🔧 Phase 1: 청킹 + 요약 시작...")

    # --- Step 1: 청킹 2-Pass (파일 단위 병렬) ---
    log_nontarget("PHASE", f"Step 1: 청킹 2-Pass ({len(files)}개 파일)")
    yield emit_message(f"   ├─ Step 1: 청킹 2-Pass ({len(files)}개 파일, 병렬)")

    chunk_results: List[Tuple[str, List]] = await asyncio.gather(*[
        _limited(run_chunker_2pass(f, ctx, deepagents_client))
        for f in files
    ])

    # 품질 게이트 + Neo4j 커밋 (순차)
    for file_path, blocks_json in chunk_results:
        if not blocks_json:
            continue
        passed, warnings = quality_gate_phase1_blocks(blocks_json, file_path)
        if passed:
            await commit_blocks(ctx, file_path, blocks_json)

    total_blocks = sum(len(b) for _, b in chunk_results)
    yield emit_message(f"   │     ✓ {total_blocks}개 블록 생성 완료")

    # --- Step 2: 요약 (파일 단위 병렬 + tiktoken 배치) ---
    log_nontarget("PHASE", f"Step 2: 요약 ({total_blocks}개 블록)")
    yield emit_message(f"   ├─ Step 2: 요약 ({total_blocks}개 블록, 배치)")

    summary_results: List[Tuple[str, List]] = await asyncio.gather(*[
        _limited(run_summarizer_batched(fp, blocks, ctx, deepagents_client))
        for fp, blocks in chunk_results
        if blocks
    ])

    # 품질 게이트 + Neo4j 커밋 (순차)
    for file_path, summaries_json in summary_results:
        if not summaries_json:
            continue
        passed, warnings = quality_gate_phase1_summaries(summaries_json, file_path)
        if passed:
            await commit_summaries(ctx, file_path, summaries_json)

            # Table/Column upsert (summary에서 추출된 테이블 정보)
            tables, columns = _extract_tables_columns(summaries_json)
            if tables:
                await commit_tables(ctx, tables)
            if columns:
                await commit_columns(ctx, columns)

    total_summaries = sum(len(s) for _, s in summary_results)
    yield emit_message(f"   │     ✓ {total_summaries}개 요약 완료")

    # --- Step 2.5: FileSummary 생성 ---
    file_summaries = build_file_summaries(chunk_results, summary_results)
    log_nontarget("PHASE", f"Step 2.5: FileSummary {len(file_summaries)}개 생성")

    # --- 벡터화 ---
    vectorized = await vectorize_all_blocks(ctx)
    if vectorized > 0:
        yield emit_message(f"   │     ✓ {vectorized}개 블록 벡터화")

    # ==================================================================
    # Phase 2: 링크 + Description
    # ==================================================================
    yield emit_message("   🔗 Phase 2: 링크 + Description 시작...")

    # --- Step 3: 링크 (파일 단위 병렬) ---
    link_files = [fp for fp in files if fp in file_summaries]
    log_nontarget("PHASE", f"Step 3: 링크 ({len(link_files)}개 파일)")
    yield emit_message(f"   ├─ Step 3: 링크 ({len(link_files)}개 파일, 병렬)")

    link_results: List[Tuple[str, dict]] = await asyncio.gather(*[
        _limited(run_linker(fp, file_summaries.get(fp, {}), ctx, deepagents_client))
        for fp in link_files
    ])

    # 품질 게이트 + Neo4j 커밋 (순차)
    total_links = 0
    for file_path, links_json in link_results:
        passed, warnings = quality_gate_phase2_links(links_json, file_path)
        if passed:
            counts = await commit_links(ctx, file_path, links_json)
            total_links += sum(counts.values())

    yield emit_message(f"   │     ✓ {total_links}개 관계 생성 완료")

    # --- Step 4: Description (엔티티 단위) ---
    log_nontarget("PHASE", "Step 4: Description (엔티티 단위)")
    yield emit_message("   ├─ Step 4: Description (엔티티 단위)")

    entity_evidence = await build_entity_evidence(ctx)

    if entity_evidence:
        desc_results = await asyncio.gather(*[
            _limited(run_description(eid, ev, file_summaries, ctx, deepagents_client))
            for eid, ev in entity_evidence.items()
        ])

        # Neo4j 커밋 (순차)
        desc_items = [
            desc for _, desc in desc_results
            if desc.get("description", "").strip()
        ]
        if desc_items:
            await commit_descriptions(ctx, desc_items)

        yield emit_message(f"   │     ✓ {len(desc_items)}개 description 갱신")
    else:
        yield emit_message("   │     ℹ️ 갱신할 엔티티 없음")

    # ==================================================================
    # 완료
    # ==================================================================
    log_nontarget("PHASE", f"Phase 3 완료: {len(files)}개 파일, {total_blocks}개 블록, {total_links}개 관계")
    yield emit_message(
        f"   ✅ 비타겟 분석 완료: {len(files)}개 파일, "
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
        for t in s.get("tables", []):
            table_name = t.get("name", "")
            if not table_name:
                continue

            tables.append({
                "name": table_name,
                "entity_name": t.get("entity_name", ""),
                "schema": t.get("schema", "public"),
                "db": t.get("db", "default"),
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
                    "schema": t.get("schema", "public"),
                })

    return tables, columns


def _init_memory_dirs():
    """mem/ 디렉토리 구조를 초기화합니다."""
    dirs = [
        "mem/context/topics",
        "mem/patterns",
        "mem/working",
        "mem/archive",
    ]
    for d in dirs:
        os.makedirs(d, exist_ok=True)

    # overview 초기화 (없으면)
    overview_path = "mem/context/overview.md"
    if not os.path.exists(overview_path):
        with open(overview_path, "w", encoding="utf-8") as f:
            f.write("# Project Context\n\n*비타겟 분석에서 자동 생성됨*\n")

    log_nontarget("PHASE", "mem/ 디렉토리 초기화 완료")
