"""Framework 분석 총괄 파이프라인 (Java/Kotlin/C/Python 등 범용).

Phase 0 (선택적): DDL 처리 (DDL 파일 있을 때만)
Phase 1: AST 정적 그래프 (스캔 + AST + 아티팩트 + 인덱스)
Phase 2: LLM 링킹 (SQL→Table, Call batch, UML, analysis)
Phase 3: 후처리 (요약 보강, Field/Class 요약, Table Description 보강)

BaseStreamingAnalyzer 구현으로 AnalyzerFactory.create("framework")로 사용 가능.
"""

import asyncio
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any, AsyncGenerator, Dict, List, Optional

from client.neo4j_client import Neo4jClient
from analyzer.strategy.base_analyzer import BaseStreamingAnalyzer, AnalysisStats
from analyzer.pipeline_control import pipeline_controller, PipelinePhase
from config.settings import settings
from util.ndjson_stream import emit_message, emit_phase_event

from analyzer.strategy.framework.pipeline.phase_ddl import run_phase_ddl
from analyzer.strategy.framework.pipeline.phase_ast import run_phase_ast
from analyzer.strategy.framework.pipeline.phase_linking import run_phase_linking
from analyzer.strategy.framework.pipeline.phase_enrichment import run_phase_enrichment
from analyzer.strategy.framework.pipeline.state import FrameworkPipelineState
from analyzer.strategy.framework.common.lang_profile import get_lang_profile

logger = logging.getLogger(__name__)


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
        self._cypher_lock = asyncio.Lock()
        self._file_semaphore: Optional[asyncio.Semaphore] = None
        self._pipeline_state = FrameworkPipelineState()

    async def run_pipeline(
        self,
        file_names: list[tuple[str, str]],
        client: Neo4jClient,
        orchestrator: Any,
        stats: AnalysisStats,
        run_dir: Optional[str] = None,
    ) -> AsyncGenerator[bytes, None]:
        self._file_semaphore = asyncio.Semaphore(settings.concurrency.file_concurrency)
        pipeline_controller.reset()
        ps = pipeline_controller.get_state()
        if not run_dir:
            run_dir = str(Path(settings.path.audit_dir) / "analysis.run")
            run_path = Path(run_dir)
            if run_path.is_dir():
                import shutil
                shutil.rmtree(run_path)
            run_path.mkdir(parents=True, exist_ok=True)
        self._pipeline_state.run_dir = run_dir

        yield emit_message(f"☕ Framework 분석 시작 | 파일 {len(file_names)}개")

        # ========== Phase 0 (선택적): DDL ==========
        from analyzer.common.ddl_utils import list_ddl_files
        ddl_files = list_ddl_files(orchestrator)
        if ddl_files:
            yield emit_phase_event(0, "DDL 스키마 수집", "started", 0)
            async for chunk in run_phase_ddl(self, client, orchestrator, stats):
                yield chunk
            yield emit_phase_event(0, "DDL 스키마 수집", "completed", 100)

        # ========== Phase 1: AST ==========
        ps.set_phase(PipelinePhase.AST_GENERATION, "AST 정적 그래프 생성 중", 0)
        yield emit_phase_event(1, "AST 정적 그래프", "started", 0, {"canPause": True})
        async for chunk in run_phase_ast(self, file_names, client, orchestrator, stats):
            yield chunk
        yield emit_phase_event(1, "AST 정적 그래프", "completed", 100)
        if run_dir:
            try:
                from analyzer.strategy.framework.artifact_dump import dump_phase1_artifacts
                dump_phase1_artifacts(self._pipeline_state, Path(run_dir))
            except Exception as e:
                logger.error("[PHASE:ARTIFACT] Phase1 산출물 덤프 실패: %s", e)
        self._pipeline_state.clear_phase_data(1)  # Phase 1 중간 데이터 해제
        if not await ps.wait_if_paused():
            yield emit_message("⏹️ 파이프라인 중단")
            return

        # ========== Phase 2: LLM 링킹 ==========
        ps.set_phase(PipelinePhase.LLM_ANALYSIS, "LLM 링킹 분석 중", 0)
        yield emit_phase_event(2, "LLM 링킹", "started", 0, {"canPause": True})
        async for chunk in run_phase_linking(self, client, orchestrator, stats):
            yield chunk
        yield emit_phase_event(2, "LLM 링킹", "completed", 100)
        if run_dir:
            try:
                from analyzer.strategy.framework.artifact_dump import dump_phase2_artifacts
                dump_phase2_artifacts(self._pipeline_state, Path(run_dir))
            except Exception as e:
                logger.error("[PHASE:ARTIFACT] Phase2 산출물 덤프 실패: %s", e)
        self._pipeline_state.clear_phase_data(2)  # Phase 2 중간 데이터 해제
        if not await ps.wait_if_paused():
            yield emit_message("⏹️ 파이프라인 중단")
            return

        # ========== Phase 3: 코드 분석 + 보강 ==========
        ps.set_phase(PipelinePhase.CODE_ENRICHMENT, "코드 분석 + 보강 중", 0)
        yield emit_phase_event(3, "코드 분석 + 보강", "started", 0, {"canPause": True})
        async for chunk in run_phase_enrichment(self, client, orchestrator, stats):
            yield chunk
        yield emit_phase_event(3, "후처리", "completed", 100)
        if run_dir:
            try:
                from analyzer.strategy.framework.artifact_dump import dump_phase3_artifacts
                dump_phase3_artifacts(self._pipeline_state, Path(run_dir))
            except Exception as e:
                logger.error("[PHASE:ARTIFACT] Phase3 산출물 덤프 실패: %s", e)

