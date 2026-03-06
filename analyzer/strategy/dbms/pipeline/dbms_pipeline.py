"""DBMS 분석 총괄 파이프라인

Phase 0~5를 순차 실행하는 파이프라인 오케스트레이터.
BaseStreamingAnalyzer 구현.
"""

import asyncio
import json
import logging
import os
from typing import Any, AsyncGenerator, Optional, List, Dict, Tuple

from client.neo4j_client import Neo4jClient
from analyzer.strategy.base_analyzer import BaseStreamingAnalyzer, AnalysisStats
from analyzer.strategy.dbms.common.models import FileStatus, FileAnalysisContext
from analyzer.pipeline_control import pipeline_controller, PipelinePhase
from config.settings import settings
from util.encoding_utils import read_text_with_fallback
from util.ndjson_stream import (
    emit_message,
    emit_phase_event,
)
from util.text_utils import (
    log_process,
    generate_user_story_document,
)

from analyzer.strategy.dbms.pipeline.phases import run_phase_ddl
from analyzer.strategy.dbms.pipeline.phases import run_phase_ast
from analyzer.strategy.dbms.pipeline.phases import run_phase_llm
from analyzer.strategy.dbms.pipeline.phase_metadata import run_phase_metadata
from analyzer.strategy.dbms.pipeline.phases import run_phase_vector
from analyzer.strategy.dbms.pipeline.phases import run_phase_lineage


class DbmsPipeline(BaseStreamingAnalyzer):
    """DBMS 분석 총괄 파이프라인

    Phase 0: DDL 처리
    Phase 1: AST 정적 그래프 생성
    Phase 2: LLM 배치 분석
    Phase 3: 테이블/컬럼 설명 보강 (표시)
    Phase 3.5: 메타데이터 보강 (Text2SQL)
    Phase 4: 벡터라이징
    Phase 5: 리니지 분석
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
        self._cypher_lock = asyncio.Lock()
        self._file_semaphore: Optional[asyncio.Semaphore] = None
        self._ddl_schemas: set[str] = set()
        self._ddl_table_metadata: Dict[Tuple[str, str], Dict[str, Any]] = {}

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
        self._file_semaphore = asyncio.Semaphore(settings.concurrency.file_concurrency)
        pipeline_controller.reset()
        pipeline_state = pipeline_controller.get_state()

        if settings.llm.cache_enabled:
            cache_path = settings.llm.cache_db_path
            if not os.path.isabs(cache_path):
                cache_path = os.path.join(settings.path.base_dir, cache_path)
            cache_exists = os.path.exists(cache_path)
            cache_size = os.path.getsize(cache_path) if cache_exists else 0
            cache_size_str = f"{cache_size / 1024:.1f}KB" if cache_size < 1024*1024 else f"{cache_size / (1024*1024):.1f}MB"
            yield emit_message(f"🗄️ LLM 캐시: 활성화 ({cache_size_str if cache_exists else '신규'})")
        else:
            yield emit_message("🔄 LLM 캐시: 비활성화 (매번 새로운 LLM 호출)")

        if total_files > 0:
            yield emit_message(f"⚡ 병렬 처리: 파일 {settings.concurrency.file_concurrency}개 동시")

        # ========== Phase 0: DDL 처리 ==========
        pipeline_state.set_phase(PipelinePhase.DDL_PROCESSING, "DDL 파일 처리 중", 0)
        yield emit_phase_event(0, "DDL 처리", "started", 0, {"canPause": True})
        async for chunk in run_phase_ddl(self, client, orchestrator, stats):
            yield chunk
        yield emit_phase_event(0, "DDL 처리", "completed", 100)
        if not await pipeline_state.wait_if_paused():
            yield emit_message("⏹️ 파이프라인이 중단되었습니다")
            pipeline_state.set_phase(PipelinePhase.CANCELLED)
            return

        # DDL만 있는 경우
        if total_files == 0:
            yield emit_message("")
            yield emit_message("📋 DDL 파일만 처리되었습니다 (소스 파일 없음 → Phase 1,2,3 스킵)")
            yield emit_message("   ➡️ Phase 3.5: 메타데이터 보강으로 진행합니다...")
            pipeline_state.set_phase(PipelinePhase.TABLE_ENRICHMENT, "메타데이터 보강 중", 0)
            yield emit_phase_event(3.5, "메타데이터 보강", "started", 0, {"canPause": True})
            yield emit_message("")
            yield self.emit_separator()
            yield self.emit_phase_header(3.5, "📋 메타데이터 보강", "샘플 데이터 기반 설명 생성")
            yield self.emit_separator()
            async for chunk in run_phase_metadata(self, client, orchestrator, stats):
                yield chunk
            yield emit_message("")
            yield self.emit_phase_complete(3.5, "메타데이터 보강 완료")
            yield emit_phase_event(3.5, "메타데이터 보강", "completed", 100)
            yield emit_message("   ➡️ Phase 4: 벡터라이징으로 진행합니다...")
            pipeline_state.set_phase(PipelinePhase.VECTORIZING, "테이블/컬럼 벡터라이징 중", 0)
            yield emit_phase_event(4, "벡터라이징", "started", 0, {"canPause": True})
            yield emit_message("")
            yield self.emit_separator()
            yield self.emit_phase_header(4, "🔢 벡터라이징", "임베딩 생성")
            yield self.emit_separator()
            async for chunk in run_phase_vector(self, client, orchestrator, stats):
                yield chunk
            yield emit_message("")
            yield self.emit_phase_complete(4, "벡터라이징 완료")
            yield emit_phase_event(4, "벡터라이징", "completed", 100, {"tables_vectorized": stats.tables_vectorized, "columns_vectorized": stats.columns_vectorized})
            pipeline_state.set_phase(PipelinePhase.COMPLETED)
            return

        # ========== Phase 1: AST 정적 그래프 ==========
        pipeline_state.set_phase(PipelinePhase.AST_GENERATION, "AST 구조 그래프 생성 중", 0)
        yield emit_phase_event(1, "AST 구조 생성", "started", 0, {"canPause": True})
        yield emit_message("")
        yield self.emit_separator()
        yield self.emit_phase_header(1, "🏗️ AST 구조 그래프 생성", f"{total_files}개 파일 병렬")
        yield self.emit_separator()
        contexts = await self._load_all_files(file_names, orchestrator)
        yield emit_message(f"   ✓ {len(contexts)}개 파일 로드 완료")
        async for chunk in run_phase_ast(self, contexts, client, orchestrator, stats):
            yield chunk
        ph1_ok_count = sum(1 for c in contexts if c.status == FileStatus.PH1_OK)
        ph1_fail_count = sum(1 for c in contexts if c.status == FileStatus.PH1_FAIL)
        yield emit_message("")
        yield self.emit_phase_complete(1, f"{stats.static_nodes_created}개 노드 생성")
        yield emit_phase_event(1, "AST 구조 생성", "completed", 100, {"nodes": stats.static_nodes_created})
        if ph1_fail_count > 0:
            yield self.emit_warning(f"Phase 1 실패: {ph1_fail_count}개 파일 → Phase 2 스킵 (토큰 절감)")
        if not await pipeline_state.wait_if_paused():
            yield emit_message("⏹️ 파이프라인이 중단되었습니다")
            pipeline_state.set_phase(PipelinePhase.CANCELLED)
            return

        # ========== Phase 2: LLM 배치 분석 ==========
        ph2_targets = [c for c in contexts if c.status == FileStatus.PH1_OK]
        pipeline_state.set_phase(PipelinePhase.LLM_ANALYSIS, "AI 분석 중", 0)
        yield emit_phase_event(2, "AI 분석", "started", 0, {"canPause": True, "files": len(ph2_targets)})
        yield emit_message("")
        yield self.emit_separator()
        yield self.emit_phase_header(2, "🤖 AI 분석", f"{len(ph2_targets)}개 파일 병렬")
        yield self.emit_separator()
        if ph1_fail_count > 0:
            yield emit_message(f"   ℹ️ {ph1_fail_count}개 파일은 Phase 1 실패로 스킵됨 (토큰 절감)")
        async for chunk in run_phase_llm(self, ph2_targets, client, orchestrator, stats):
            yield chunk
        yield emit_message("")
        yield self.emit_phase_complete(2, f"{stats.llm_batches_executed}개 분석 완료")
        yield emit_phase_event(2, "AI 분석", "completed", 100, {"batches": stats.llm_batches_executed})
        if not await pipeline_state.wait_if_paused():
            yield emit_message("⏹️ 파이프라인이 중단되었습니다")
            pipeline_state.set_phase(PipelinePhase.CANCELLED)
            return

        # ========== Phase 3: 테이블/컬럼 설명 보강 (표시) ==========
        pipeline_state.set_phase(PipelinePhase.TABLE_ENRICHMENT, "테이블/컬럼 설명 보강 중", 0)
        yield emit_phase_event(3, "테이블 설명 보강", "started", 0, {"canPause": True})
        yield self.emit_phase_header(3, "📊 테이블/컬럼 설명 보강", "LLM 분석 결과 적용")
        yield emit_message(f"   ✅ 테이블/컬럼 설명 보강 완료")
        yield self.emit_phase_complete(3, "설명 보강 완료")
        yield emit_phase_event(3, "테이블 설명 보강", "completed", 100)
        if not await pipeline_state.wait_if_paused():
            yield emit_message("⏹️ 파이프라인이 중단되었습니다")
            pipeline_state.set_phase(PipelinePhase.CANCELLED)
            return

        # ========== Phase 3.5: 메타데이터 보강 ==========
        yield emit_message("")
        yield self.emit_separator()
        yield self.emit_phase_header(3.5, "📋 메타데이터 보강", "Text2SQL 기반 설명 생성")
        yield self.emit_separator()
        async for chunk in run_phase_metadata(self, client, orchestrator, stats):
            yield chunk
        yield emit_message("")
        if not await pipeline_state.wait_if_paused():
            yield emit_message("⏹️ 파이프라인이 중단되었습니다")
            pipeline_state.set_phase(PipelinePhase.CANCELLED)
            return

        # ========== Phase 4: 벡터라이징 ==========
        pipeline_state.set_phase(PipelinePhase.VECTORIZING, "테이블/컬럼 벡터라이징 중", 0)
        yield emit_phase_event(4, "벡터라이징", "started", 0, {"canPause": True})
        yield emit_message("")
        yield self.emit_separator()
        yield self.emit_phase_header(4, "🔢 벡터라이징", "임베딩 생성")
        yield self.emit_separator()
        async for chunk in run_phase_vector(self, client, orchestrator, stats):
            yield chunk
        yield emit_message("")
        yield self.emit_phase_complete(4, "벡터라이징 완료")
        yield emit_phase_event(4, "벡터라이징", "completed", 100, {"tables_vectorized": stats.tables_vectorized, "columns_vectorized": stats.columns_vectorized})
        if not await pipeline_state.wait_if_paused():
            yield emit_message("⏹️ 파이프라인이 중단되었습니다")
            pipeline_state.set_phase(PipelinePhase.CANCELLED)
            return

        # ========== Phase 5: 리니지 분석 ==========
        pipeline_state.set_phase(PipelinePhase.LINEAGE_ANALYSIS, "데이터 리니지 분석 중", 0)
        yield emit_phase_event(5, "리니지 분석", "started", 0, {"canPause": True})
        yield emit_message("")
        yield self.emit_separator()
        yield self.emit_phase_header(5, "🔗 데이터 리니지 분석", "ETL 패턴 감지")
        yield self.emit_separator()
        async for chunk in run_phase_lineage(self, client, orchestrator, stats):
            yield chunk
        yield emit_message("")
        yield self.emit_phase_complete(5, "리니지 분석 완료")
        yield emit_phase_event(5, "리니지 분석", "completed", 100, {"etl_count": getattr(stats, 'etl_count', 0), "data_flows": getattr(stats, 'data_flows', 0)})
        if not await pipeline_state.wait_if_paused():
            yield emit_message("⏹️ 파이프라인이 중단되었습니다")
            pipeline_state.set_phase(PipelinePhase.CANCELLED)
            return

    async def build_user_story_doc(self, client: Neo4jClient, orchestrator: Any) -> Optional[str]:
        """분석된 프로시저에서 User Story 문서 생성"""
        query = """
            MATCH (__cy_n__)
            WHERE (__cy_n__:PROCEDURE OR __cy_n__:FUNCTION OR __cy_n__:TRIGGER)
              AND __cy_n__.summary IS NOT NULL
            OPTIONAL MATCH (__cy_n__)-[:HAS_USER_STORY]->(__cy_us__:UserStory)
            OPTIONAL MATCH (__cy_us__)-[:HAS_AC]->(__cy_ac__:AcceptanceCriteria)
            WITH __cy_n__, __cy_us__, collect(DISTINCT {id: __cy_ac__.id, title: __cy_ac__.title, given: __cy_ac__.given, when: __cy_ac__.when, then: __cy_ac__.then}) AS acceptance_criteria
            WITH __cy_n__, collect(DISTINCT {id: __cy_us__.id, role: __cy_us__.role, goal: __cy_us__.goal, benefit: __cy_us__.benefit, acceptance_criteria: acceptance_criteria}) AS user_stories
            RETURN __cy_n__.procedure_name AS name, __cy_n__.summary AS summary, user_stories AS user_stories, labels(__cy_n__)[0] AS type
            ORDER BY __cy_n__.file_name, __cy_n__.startLine
        """
        async with self._cypher_lock:
            results = await client.execute_queries([query])
        if not results or not results[0]:
            log_process("ANALYZE", "USER_STORY", "User Story 생성 스킵: 분석된 프로시저/함수가 없습니다", logging.INFO)
            return None
        filtered = [r for r in results[0] if r.get("summary") or (r.get("user_stories") and len(r["user_stories"]) > 0)]
        if not filtered:
            return None
        log_process("ANALYZE", "USER_STORY", f"User Story 생성 | 대상={len(filtered)}개 프로시저")
        return generate_user_story_document(results=filtered, source_name="ROBO", source_type="DBMS 프로시저/함수")

    async def _load_all_files(self, file_names: list[tuple[str, str]], orchestrator: Any) -> List[FileAnalysisContext]:
        """모든 파일의 AST JSON과 원본 소스를 병렬로 로드 (AST에 code 없음 → source_content로 슬라이스)"""
        async def load_single(directory: str, file_name: str) -> FileAnalysisContext:
            base_name = os.path.splitext(file_name)[0]
            ast_path = os.path.join(orchestrator.dirs["analysis"], "target", directory, f"{base_name}.json")
            ast_content, encoding_used = await read_text_with_fallback(ast_path, preferred_encodings=("utf-8", "utf-8-sig", "cp949", "euc-kr"))
            if encoding_used != "utf-8":
                logging.info("[FILE_ENCODING] AST JSON fallback decoding: %s (encoding=%s)", ast_path, encoding_used)
            source_content = ""
            source_dir = orchestrator.dirs.get("source", orchestrator.dirs["analysis"])
            source_path = os.path.join(source_dir, directory, file_name)
            if os.path.isfile(source_path):
                try:
                    source_content, _ = await read_text_with_fallback(source_path, preferred_encodings=("utf-8", "utf-8-sig", "cp949", "euc-kr"))
                except Exception as e:
                    logging.warning("[DBMS] 원본 소스 읽기 실패 (source_content 비움): %s - %s", source_path, e)
            return FileAnalysisContext(directory=directory, file_name=file_name, ast_data=json.loads(ast_content), source_content=source_content)
        return await asyncio.gather(*[load_single(d, f) for d, f in file_names])


