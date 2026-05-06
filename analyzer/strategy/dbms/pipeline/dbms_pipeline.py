"""DBMS 분석 총괄 파이프라인

전체 모드(Phase 0~5) 또는 DDL-only 모드(Phase 0~2)를 순차 실행.
BaseStreamingAnalyzer 구현.
"""

import asyncio
import json
import logging
import os
from typing import Any, AsyncGenerator, Optional, List

from client.neo4j_client import Neo4jClient
from analyzer.strategy.base_analyzer import BaseStreamingAnalyzer, AnalysisStats
from analyzer.strategy.dbms.common.models.node_models import FileStatus, FileAnalysisContext
from analyzer.common.pipeline.pipeline_control import pipeline_controller, PipelinePhase
from config.settings import settings
from util.encoding_reader import read_text_with_fallback
from util.stream_emitter import (
    emit_user_message,
    emit_phase_event,
    emit_separator,
    emit_phase_header,
    emit_phase_complete,
)

from analyzer.strategy.dbms.pipeline.phase_analysis import run_phase_llm
from analyzer.strategy.dbms.pipeline.phase_ast import run_phase_ast
from analyzer.strategy.dbms.pipeline.phase_ddl import run_phase_ddl
from analyzer.strategy.dbms.pipeline.phase_lineage import run_phase_lineage
from analyzer.strategy.dbms.pipeline.phase_linking import run_phase_linking
from analyzer.strategy.dbms.pipeline.phase_metadata import run_phase_metadata
from analyzer.strategy.dbms.pipeline.phase_vector import run_phase_vector
from analyzer.strategy.dbms.pipeline.pipeline_state import DbmsPhaseContext
from analyzer.common.pipeline.stream_merge import merge_streams


class DbmsPipeline(BaseStreamingAnalyzer):
    """DBMS 분석 총괄 파이프라인

    [전체 모드] Phase 0~5:
      Phase 0: DDL 처리
      Phase 1: AST 정적 그래프 생성
      Phase 2: LLM 배치 분석
      Phase 3: 메타데이터 보강 (Text2SQL)
      Phase 4: 벡터라이징
      Phase 5: 리니지 분석

    [DDL-only 모드] Phase 0~2:
      Phase 0: DDL 처리
      Phase 1: 메타데이터 보강
      Phase 2: 벡터라이징
    """

    @property
    def strategy_name(self) -> str:
        return "DBMS"

    @property
    def strategy_emoji(self) -> str:
        return "🗄️"

    @property
    def file_type_description(self) -> str:
        return "SQL 파일"

    def __init__(self):
        # 동시성 제어 + DDL 누적 상태는 state 단일 출처(DbmsPipelineState)로 보관.
        # run_pipeline 진입 시 PhaseContext.create() 가 state 초기화 후 phase 들에 전달.
        pass

    async def run_pipeline(
        self,
        file_names: list[tuple[str, str]],
        client: Neo4jClient,
        orchestrator: Any,
        stats: AnalysisStats,
        run_dir: Optional[str] = None,
    ) -> AsyncGenerator[bytes, None]:
        """DBMS 분석 파이프라인 실행 - Phase 0~5 순차 실행. run_dir은 Framework 전용(산출물 저장), DBMS에서는 미사용."""
        total_files = len(file_names)
        file_semaphore = asyncio.Semaphore(settings.concurrency.file_concurrency)
        pipeline_controller.reset()
        pipeline_state = pipeline_controller.get_state()

        if settings.llm.cache_enabled:
            cache_path = settings.llm.cache_db_path
            if not os.path.isabs(cache_path):
                cache_path = os.path.join(settings.path.base_dir, cache_path)
            cache_exists = os.path.exists(cache_path)
            cache_size = os.path.getsize(cache_path) if cache_exists else 0
            cache_size_str = f"{cache_size / 1024:.1f}KB" if cache_size < 1024*1024 else f"{cache_size / (1024*1024):.1f}MB"
            yield emit_user_message(f"🗄️ LLM 캐시: 활성화 ({cache_size_str if cache_exists else '신규'})")
        else:
            yield emit_user_message("🔄 LLM 캐시: 비활성화 (매번 새로운 LLM 호출)")

        if total_files > 0:
            yield emit_user_message(f"⚡ 병렬 처리: 파일 {settings.concurrency.file_concurrency}개 동시")

        # PhaseContext 생성 (state 단일 출처 — pipeline 인스턴스 참조 없음)
        ctx = DbmsPhaseContext.create(client, orchestrator, stats, file_semaphore)

        # ========== Phase 0: DDL 처리 ==========
        # totalPhases: DDL-only=3(Phase 0~2), 전체=5(Phase 0~4)
        # — Phase 3 = 메타 보강 + 리니지 (병렬, 단일 num), Phase 4 = 벡터라이징
        total_phases = 3 if total_files == 0 else 5
        pipeline_state.set_phase(PipelinePhase.DDL_PROCESSING, "DDL 파일 처리 중", 0)
        yield emit_phase_event(0, "DDL 처리", "started", 0, {"canPause": True, "totalPhases": total_phases})
        async for chunk in run_phase_ddl(ctx):
            yield chunk
        yield emit_phase_event(0, "DDL 처리", "completed", 100)
        if not await pipeline_state.wait_if_paused():
            yield emit_user_message("⏹️ 파이프라인이 중단되었습니다")
            pipeline_state.set_phase(PipelinePhase.CANCELLED)
            return

        # DDL만 있는 경우 (Phase 0→1→2)
        if total_files == 0:
            yield emit_user_message("")
            yield emit_user_message("📋 DDL 파일만 처리되었습니다 (소스 파일 없음)")
            yield emit_user_message("   ➡️ Phase 1: 메타데이터 보강으로 진행합니다...")
            pipeline_state.set_phase(PipelinePhase.CODE_ENRICHMENT, "메타데이터 보강 중", 0)
            yield emit_phase_event(1, "메타데이터 보강", "started", 0, {"canPause": True})
            yield emit_user_message("")
            yield emit_separator()
            yield emit_phase_header(1, "📋 메타데이터 보강", "샘플 데이터 기반 설명 생성")
            yield emit_separator()
            async for chunk in run_phase_metadata(ctx, phase_num=1):
                yield chunk
            yield emit_user_message("")
            yield emit_phase_complete(1, "메타데이터 보강 완료")
            yield emit_phase_event(1, "메타데이터 보강", "completed", 100)
            yield emit_user_message("   ➡️ Phase 2: 벡터라이징으로 진행합니다...")
            pipeline_state.set_phase(PipelinePhase.VECTORIZING, "테이블/컬럼 벡터라이징 중", 0)
            yield emit_phase_event(2, "벡터라이징", "started", 0, {"canPause": True})
            yield emit_user_message("")
            yield emit_separator()
            yield emit_phase_header(2, "🔢 벡터라이징", "임베딩 생성")
            yield emit_separator()
            async for chunk in run_phase_vector(ctx, phase_num=2):
                yield chunk
            yield emit_user_message("")
            yield emit_phase_complete(2, "벡터라이징 완료")
            yield emit_phase_event(2, "벡터라이징", "completed", 100, {"tables_vectorized": stats.tables_vectorized, "columns_vectorized": stats.columns_vectorized})
            pipeline_state.set_phase(PipelinePhase.COMPLETED)
            return

        # ========== Phase 1: AST 정적 그래프 ==========
        pipeline_state.set_phase(PipelinePhase.AST_GENERATION, "AST 구조 그래프 생성 중", 0)
        yield emit_phase_event(1, "AST 구조 생성", "started", 0, {"canPause": True})
        yield emit_user_message("")
        yield emit_separator()
        yield emit_phase_header(1, "🏗️ AST 구조 그래프 생성", f"{total_files}개 파일 병렬")
        yield emit_separator()
        contexts = await self._load_all_files(file_names, orchestrator)
        yield emit_user_message(f"   ✓ {len(contexts)}개 파일 로드 완료")
        async for chunk in run_phase_ast(ctx, contexts):
            yield chunk
        ph1_ok_count = sum(1 for c in contexts if c.status == FileStatus.PH1_OK)
        ph1_fail_count = sum(1 for c in contexts if c.status == FileStatus.PH1_FAIL)
        yield emit_user_message("")
        yield emit_phase_complete(1, f"{stats.static_nodes_created}개 노드 생성")
        yield emit_phase_event(1, "AST 구조 생성", "completed", 100, {"nodes": stats.static_nodes_created})
        if ph1_fail_count > 0:
            yield emit_user_message("⚠️ " + f"Phase 1 실패: {ph1_fail_count}개 파일 → Phase 2 스킵 (토큰 절감)")
        if not await pipeline_state.wait_if_paused():
            yield emit_user_message("⏹️ 파이프라인이 중단되었습니다")
            pipeline_state.set_phase(PipelinePhase.CANCELLED)
            return

        # ========== Phase 1.5: 결정론 링킹 (AST 기반 CALLS + REFERENCES) ==========
        async for chunk in run_phase_linking(ctx, contexts):
            yield chunk

        # ========== Phase 2: LLM 배치 분석 ==========
        ph2_targets = [c for c in contexts if c.status == FileStatus.PH1_OK]
        pipeline_state.set_phase(PipelinePhase.LLM_ANALYSIS, "AI 분석 중", 0)
        yield emit_phase_event(2, "AI 분석", "started", 0, {"canPause": True, "files": len(ph2_targets)})
        yield emit_user_message("")
        yield emit_separator()
        yield emit_phase_header(2, "🤖 AI 분석", f"{len(ph2_targets)}개 파일 병렬")
        yield emit_separator()
        if ph1_fail_count > 0:
            yield emit_user_message(f"   ℹ️ {ph1_fail_count}개 파일은 Phase 1 실패로 스킵됨 (토큰 절감)")
        async for chunk in run_phase_llm(ctx, ph2_targets):
            yield chunk
        yield emit_user_message("")
        yield emit_phase_complete(2, f"{stats.llm_batches_executed}개 분석 완료")
        yield emit_phase_event(2, "AI 분석", "completed", 100, {"batches": stats.llm_batches_executed})
        if not await pipeline_state.wait_if_paused():
            yield emit_user_message("⏹️ 파이프라인이 중단되었습니다")
            pipeline_state.set_phase(PipelinePhase.CANCELLED)
            return

        # ========== Phase 3: 메타 보강 + 리니지 (병렬) ==========
        # 의존성 분석:
        #   - Phase 3 (metadata) : Phase 2 의 Neo4j Table 노드를 catalog 샘플 + LLM 으로 보강
        #   - Phase 5 (lineage)  : source 디렉토리 .sql 만 catalog 에 보냄 (Neo4j 의존 0)
        #   - 둘 다 catalog 호출 — 서로 의존성 X → 동시 실행으로 catalog/network IO 누적 회피
        # 동시 실행 단계는 단일 phase_event num + 통합 name 으로 emit (frontend 1 step 으로 인식).
        pipeline_state.set_phase(PipelinePhase.CODE_ENRICHMENT, "메타 보강 + 리니지 분석 중", 0)
        yield emit_phase_event(3, "메타 보강 + 리니지", "started", 0, {"canPause": True})
        yield emit_user_message("")
        yield emit_separator()
        yield emit_phase_header(3, "📋 메타 보강 + 🔗 리니지 분석", "catalog 동시 호출")
        yield emit_separator()
        async for chunk in merge_streams(
            ("metadata", run_phase_metadata(ctx, phase_num=3)),
            ("lineage",  run_phase_lineage(ctx)),
        ):
            yield chunk
        yield emit_user_message("")
        yield emit_phase_complete(3, "메타 보강 + 리니지 완료")
        yield emit_phase_event(
            3, "메타 보강 + 리니지", "completed", 100,
            {
                "etl_count": stats.lineage_etl_count,
                "etl_reads": stats.lineage_etl_reads,
                "etl_writes": stats.lineage_etl_writes,
                "data_flows": stats.lineage_data_flows,
                "files_failed": stats.lineage_files_failed,
            },
        )
        if not await pipeline_state.wait_if_paused():
            yield emit_user_message("⏹️ 파이프라인이 중단되었습니다")
            pipeline_state.set_phase(PipelinePhase.CANCELLED)
            return

        # ========== Phase 4: 벡터라이징 (Phase 3 결과 = Table.analyzed_description 사용) ==========
        pipeline_state.set_phase(PipelinePhase.VECTORIZING, "벡터라이징 중", 0)
        yield emit_phase_event(4, "벡터라이징", "started", 0, {"canPause": True})
        yield emit_user_message("")
        yield emit_separator()
        yield emit_phase_header(4, "🔢 벡터라이징", "Table + Column 임베딩")
        yield emit_separator()
        async for chunk in run_phase_vector(ctx):
            yield chunk
        yield emit_user_message("")
        yield emit_phase_complete(4, "벡터라이징 완료")
        yield emit_phase_event(
            4, "벡터라이징", "completed", 100,
            {"tables_vectorized": stats.tables_vectorized, "columns_vectorized": stats.columns_vectorized},
        )
        if not await pipeline_state.wait_if_paused():
            yield emit_user_message("⏹️ 파이프라인이 중단되었습니다")
            pipeline_state.set_phase(PipelinePhase.CANCELLED)
            return

    async def _load_all_files(self, file_names: list[tuple[str, str]], orchestrator: Any) -> List[FileAnalysisContext]:
        """모든 파일의 AST JSON과 원본 소스를 병렬로 로드 (AST에 code 없음 → source_content로 슬라이스)"""
        async def load_single(directory: str, file_name: str) -> FileAnalysisContext:
            base_name = os.path.splitext(file_name)[0]
            ast_path = os.path.join(orchestrator.dirs["analysis"], "target", directory, f"{base_name}.json")
            ast_content, encoding_used = await read_text_with_fallback(ast_path, preferred_encodings=("utf-8", "utf-8-sig", "cp949", "euc-kr"))
            if encoding_used != "utf-8":
                logging.info("[FILE_ENCODING] AST JSON fallback decoding: %s (encoding=%s)", ast_path, encoding_used)
            source_content = ""
            # source_dir 미존재는 정상 시나리오 (DBMS analysis 만 있는 경우) — analysis 디렉토리에서 직접 슬라이스 시도
            source_dir = orchestrator.dirs.get("source") or orchestrator.dirs["analysis"]
            source_path = os.path.join(source_dir, directory, file_name)
            if os.path.isfile(source_path):
                try:
                    source_content, _ = await read_text_with_fallback(source_path, preferred_encodings=("utf-8", "utf-8-sig", "cp949", "euc-kr"))
                except Exception as e:  # noqa: SILENT_FAIL — 소스 보강은 옵셔널 (AST JSON 의 code 가 우선), 읽기 실패는 errlog 후 진행
                    logging.error("[DBMS] 원본 소스 읽기 실패 (source_content 비움): %s | %s: %s", source_path, type(e).__name__, e)
            return FileAnalysisContext(directory=directory, file_name=file_name, ast_data=json.loads(ast_content), source_content=source_content)
        return await asyncio.gather(*[load_single(d, f) for d, f in file_names])


