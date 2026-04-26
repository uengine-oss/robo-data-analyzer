"""Framework 분석 총괄 파이프라인 (Java/Kotlin/C/Python 등 범용).

Phase 0 (선택적): DDL 처리 (DDL 파일 있을 때만)
Phase 1: AST 정적 그래프 (스캔 + AST + 아티팩트 + 인덱스)
Phase 2: LLM 링킹 (SQL→Table, Call batch, UML, analysis)
Phase 3: 후처리 (요약 보강, Field/Class 요약, Table Description 보강)

BaseStreamingAnalyzer 구현으로 AnalyzerFactory.create("framework")로 사용 가능.
"""

import asyncio
import logging
import shutil
from pathlib import Path
from typing import Any, AsyncGenerator, Optional

import aiohttp

from analyzer.common.cypher.ddl_handler import list_ddl_files
from analyzer.common.pipeline.pipeline_control import PipelinePhase, pipeline_controller
from analyzer.strategy.base_analyzer import AnalysisStats, BaseStreamingAnalyzer
from analyzer.strategy.framework.common.domain.lang_profile import get_lang_profile
from analyzer.strategy.framework.pipeline.debug_artifact_writer import (
    dump_phase1_artifacts,
    dump_phase2_artifacts,
    dump_phase3_artifacts,
)
from analyzer.strategy.framework.pipeline.phase_analysis import collect_analysis_context, run_code_analysis
from analyzer.strategy.framework.pipeline.phase_ast import run_phase_ast
from analyzer.strategy.framework.pipeline.phase_ddl import run_phase_ddl
from analyzer.strategy.framework.pipeline.phase_linking import run_phase_linking
from analyzer.strategy.framework.pipeline.phase_summary import run_actor_and_table_enrichment, run_field_and_module_analysis
from analyzer.strategy.framework.pipeline.pipeline_state import FrameworkPhaseContext, FrameworkPipelineState
from client.neo4j_client import Neo4jClient
from config.settings import settings
from util.stream_emitter import PhaseTracker, emit_message, emit_phase_event

logger = logging.getLogger(__name__)


# lang 이 결정되는 시점은 Phase 1 codebase_profiler. 그 전 (분석 시작 메시지) 에는
# pipeline_state.lang 이 "" 일 수 있어 generic 표시값으로 진행 — 사용자 표시 메시지 한정
# fallback 이고 분석 결과나 데이터 정확도에는 영향 없음.
def _get_lang_emoji(lang: str) -> str:
    if not lang:
        return "📄"
    return get_lang_profile().emoji(lang)


def _get_lang_desc(lang: str) -> str:
    if not lang:
        return "소스코드"
    return get_lang_profile().display_name(lang)


class FrameworkPipeline(BaseStreamingAnalyzer):
    """Framework 분석 총괄 파이프라인 (범용 — Java/Kotlin/C/Python 등).

    Phase 0 (선택적): DDL 처리 (DDL 파일 있을 때만)
    Phase 1: AST 정적 그래프 (스캔 + AST + 아티팩트 + 인덱스)
    Phase 2: LLM 링킹 (SQL→Table, Call batch, UML, analysis LLM)
    Phase 3: 후처리 (요약 보강, Field/Class 요약, Table Description 보강)
    """

    @property
    def strategy_name(self) -> str:
        return "Framework"

    @property
    def strategy_emoji(self) -> str:
        return _get_lang_emoji(self._pipeline_state.lang)

    @property
    def file_type_description(self) -> str:
        return f"{_get_lang_desc(self._pipeline_state.lang)} 파일"

    def __init__(self):
        # state 단일 출처(FrameworkPipelineState) — strategy_emoji/file_type_description property 가
        # state.lang 참조하기 위해 인스턴스 attr 1개만 유지. 동시성 제어/누적 데이터 모두 state 안에.
        self._pipeline_state = FrameworkPipelineState()

    async def run_pipeline(
        self,
        file_names: list[tuple[str, str]],
        client: Neo4jClient,
        orchestrator: Any,
        stats: AnalysisStats,
        run_dir: Optional[str] = None,
    ) -> AsyncGenerator[bytes, None]:
        cypher_lock = asyncio.Lock()
        file_semaphore = asyncio.Semaphore(settings.concurrency.file_concurrency)
        pipeline_controller.reset()
        ps = pipeline_controller.get_state()
        if not run_dir:
            run_dir = str(Path(settings.path.audit_dir) / "analysis.run")
            run_path = Path(run_dir)
            if run_path.is_dir():
                shutil.rmtree(run_path)
            run_path.mkdir(parents=True, exist_ok=True)
        self._pipeline_state.run_dir = run_dir

        # PhaseContext 생성 (state 단일 출처 — pipeline 인스턴스 참조 없음)
        ctx = FrameworkPhaseContext.create(
            client=client,
            orchestrator=orchestrator,
            state=self._pipeline_state,
            stats=stats,
            cypher_lock=cypher_lock,
            file_semaphore=file_semaphore,
        )

        yield emit_message(f"☕ Framework 분석 시작 | 파일 {len(file_names)}개")

        # ========== Phase 0 (선택적): DDL ==========
        ddl_files = list_ddl_files(orchestrator)
        if ddl_files:
            yield emit_phase_event(0, "DDL 스키마 수집", "started", 0)
            async for chunk in run_phase_ddl(ctx):
                yield chunk
            yield emit_phase_event(0, "DDL 스키마 수집", "completed", 100)

        # ========== Phase 1: AST ==========
        ps.set_phase(PipelinePhase.AST_GENERATION, "AST 정적 그래프 생성 중", 0)
        yield emit_phase_event(1, "AST 정적 그래프", "started", 0, {"canPause": True})
        async for chunk in run_phase_ast(ctx, file_names):
            yield chunk
        yield emit_phase_event(1, "AST 정적 그래프", "completed", 100)
        if run_dir:
            try:
                dump_phase1_artifacts(self._pipeline_state, Path(run_dir))
            except Exception as e:  # noqa: SILENT_FAIL — debug 산출물 덤프 실패는 분석 결과를 막지 않도록 errlog 만
                logger.error("[PHASE:ARTIFACT] Phase1 산출물 덤프 실패: %s", e)
        self._pipeline_state.clear_phase_data(1)  # Phase 1 중간 데이터 해제
        if not await ps.wait_if_paused():
            yield emit_message("⏹️ 파이프라인 중단")
            return

        # ========== Phase 2: LLM 링킹 ==========
        ps.set_phase(PipelinePhase.LLM_ANALYSIS, "LLM 링킹 분석 중", 0)
        yield emit_phase_event(2, "LLM 링킹", "started", 0, {"canPause": True})
        async for chunk in run_phase_linking(ctx):
            yield chunk
        yield emit_phase_event(2, "LLM 링킹", "completed", 100)
        if run_dir:
            try:
                dump_phase2_artifacts(self._pipeline_state, Path(run_dir))
            except Exception as e:  # noqa: SILENT_FAIL — debug 산출물 덤프 실패는 분석 결과를 막지 않도록 errlog 만
                logger.error("[PHASE:ARTIFACT] Phase2 산출물 덤프 실패: %s", e)
        self._pipeline_state.clear_phase_data(2)  # Phase 2 중간 데이터 해제
        if not await ps.wait_if_paused():
            yield emit_message("⏹️ 파이프라인 중단")
            return

        # ========== Phase 3-1: 코드 분석 (LLM) ==========
        ps.set_phase(PipelinePhase.CODE_ENRICHMENT, "코드 분석 중", 0)
        yield emit_phase_event(3, "코드 분석", "started", 0, {"canPause": True})

        tracker = PhaseTracker(2, "코드 분석 + 보강", total_phases=3)
        tracker.define_steps([
            ("코드 분석", 35),
            ("Field 분석", 15),
            ("Module 분석", 25),
            ("Table Description 보강", 25),
        ])

        summary_ctx = await collect_analysis_context(ctx)
        async for chunk in run_code_analysis(ctx, tracker, summary_ctx):
            yield chunk
        yield emit_phase_event(3, "코드 분석", "completed", 50)

        # ========== Phase 3-2: 모듈 요약 + 테이블 설명 보강 ==========
        yield emit_phase_event(4, "요약 + 보강", "started", 50, {"canPause": True})
        async for chunk in run_field_and_module_analysis(ctx, tracker):
            yield chunk
        async for chunk in run_actor_and_table_enrichment(ctx, tracker):
            yield chunk
        yield emit_phase_event(4, "요약 + 보강", "completed", 100)

        st = self._pipeline_state
        yield emit_message(
            f"✅ Phase 3 완료 | 메서드분석 {len(st.analysis_by_function)}개, "
            f"필드분석 {len(st.summary_by_variable)}개, 클래스분석 {len(st.summary_by_module)}개"
        )

        # catalog 메타데이터 보강 트리거 (fire-and-forget POST, 5s 타임아웃).
        # 트리거 실패는 errlog only — 분석 결과는 이미 끝나 있음 (silent_fail 정당화).
        if orchestrator.datasource_name:
            try:
                async with aiohttp.ClientSession() as _session:
                    await _session.post(
                        f"{settings.catalog.service_url}/robo/schema/enrich-metadata",
                        json={"datasource_name": orchestrator.datasource_name},
                        timeout=aiohttp.ClientTimeout(total=5),
                    )
                logger.info("[PHASE:3:CATALOG] 메타데이터 보강 트리거 전송 완료")
            except Exception as e:  # noqa: SILENT_FAIL — fire-and-forget 트리거, 분석 결과는 이미 끝남
                logger.error("[PHASE:3:CATALOG] 보강 트리거 실패 | error=%s", e)

        if run_dir:
            try:
                dump_phase3_artifacts(self._pipeline_state, Path(run_dir))
            except Exception as e:  # noqa: SILENT_FAIL — debug 산출물 덤프 실패는 분석 결과를 막지 않도록 errlog 만
                logger.error("[PHASE:ARTIFACT] Phase3 산출물 덤프 실패: %s", e)
