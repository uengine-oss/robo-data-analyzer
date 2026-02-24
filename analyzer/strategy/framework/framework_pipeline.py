"""Framework 분석 총괄 파이프라인

Phase 1~3 순차 실행. BaseStreamingAnalyzer 구현.
"""

import asyncio
import json
import logging
import os
from typing import Any, AsyncGenerator, Optional, List

from client.neo4j_client import Neo4jClient
from util.encoding_utils import read_text_with_fallback
from analyzer.strategy.base_analyzer import BaseStreamingAnalyzer, AnalysisStats
from analyzer.strategy.base.file_context import FileStatus, FileAnalysisContext
from analyzer.pipeline_control import pipeline_controller, PipelinePhase
from config.settings import settings
from util.stream_event import emit_message
from util.text_utils import (
    generate_user_story_document,
    log_process,
)

from analyzer.strategy.framework.phase_ast import run_phase1
from analyzer.strategy.framework.uml_relations import run_phase1_5_association
from analyzer.strategy.framework.phase_llm import run_phase2
from analyzer.strategy.framework.phase_nontarget import run_phase_nontarget


class FrameworkPipeline(BaseStreamingAnalyzer):
    """Java/Framework 코드 분석 총괄 파이프라인
    
    Phase 1: AST 그래프 생성 (병렬)
    Phase 1.5: UML ASSOCIATION 생성
    Phase 2: LLM 분석 (병렬)
    Phase 3: 비타겟 파일 분석
    """

    @property
    def strategy_name(self) -> str:
        return "프레임워크"

    @property
    def strategy_emoji(self) -> str:
        return "🚀"

    @property
    def file_type_description(self) -> str:
        return "Java/Kotlin 파일"

    def __init__(self):
        self._cypher_lock = asyncio.Lock()
        self._file_semaphore: Optional[asyncio.Semaphore] = None

    async def run_pipeline(
        self,
        file_names: list[tuple[str, str]],
        client: Neo4jClient,
        orchestrator: Any,
        stats: AnalysisStats,
    ) -> AsyncGenerator[bytes, None]:
        """Framework 분석 파이프라인 실행"""
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

        yield emit_message(f"⚡ 병렬 처리: 파일 {settings.concurrency.file_concurrency}개 동시")

        pipeline_state.set_phase(PipelinePhase.AST_GENERATION, "AST 구조 그래프 생성 중", 0)
        yield emit_message("")
        yield self.emit_separator()
        yield self.emit_phase_header(1, "🏗️ AST 구조 그래프 생성", f"{total_files}개 파일 병렬")
        yield self.emit_separator()

        contexts = await self._load_all_files(file_names, orchestrator)
        yield emit_message(f"   ✓ {len(contexts)}개 파일 로드 완료")

        async for chunk in run_phase1(self, contexts, client, orchestrator, stats):
            yield chunk

        ph1_fail_count = sum(1 for c in contexts if c.status == FileStatus.PH1_FAIL)
        yield emit_message("")
        yield self.emit_phase_complete(1, f"{stats.static_nodes_created}개 노드 생성")
        if ph1_fail_count > 0:
            yield self.emit_warning(f"Phase 1 실패: {ph1_fail_count}개 파일 → Phase 2 스킵 (토큰 절감)")

        if not await pipeline_state.wait_if_paused():
            yield emit_message("⏹️ 파이프라인이 중단되었습니다")
            pipeline_state.set_phase(PipelinePhase.CANCELLED)
            return

        ph1_5_targets = [c for c in contexts if c.status == FileStatus.PH1_OK]
        pipeline_state.set_phase(PipelinePhase.AST_GENERATION, "UML ASSOCIATION 생성 중", 0)
        yield emit_message("")
        yield self.emit_separator()
        yield self.emit_phase_header(1.5, "🔗 UML ASSOCIATION 생성", f"{len(ph1_5_targets)}개 파일 병렬")
        yield self.emit_separator()

        async for chunk in run_phase1_5_association(self, ph1_5_targets, client, orchestrator, stats):
            yield chunk

        yield emit_message("")
        yield self.emit_phase_complete(1.5, "ASSOCIATION 관계 생성 완료")

        if not await pipeline_state.wait_if_paused():
            yield emit_message("⏹️ 파이프라인이 중단되었습니다")
            pipeline_state.set_phase(PipelinePhase.CANCELLED)
            return

        ph2_targets = [c for c in contexts if c.status == FileStatus.PH1_OK]
        pipeline_state.set_phase(PipelinePhase.LLM_ANALYSIS, "AI 분석 중", 0)
        yield emit_message("")
        yield self.emit_separator()
        yield self.emit_phase_header(2, "🤖 AI 분석", f"{len(ph2_targets)}개 파일 병렬")
        yield self.emit_separator()

        if ph1_fail_count > 0:
            yield emit_message(f"   ℹ️ {ph1_fail_count}개 파일은 Phase 1 실패로 스킵됨 (토큰 절감)")

        async for chunk in run_phase2(self, ph2_targets, client, orchestrator, stats):
            yield chunk

        yield emit_message("")
        yield self.emit_phase_complete(2, f"{stats.llm_batches_executed}개 분석 완료")

        if not await pipeline_state.wait_if_paused():
            yield emit_message("⏹️ 파이프라인이 중단되었습니다")
            pipeline_state.set_phase(PipelinePhase.CANCELLED)
            return

        nontarget_files = [(d, f) for d, f in file_names if not f.endswith(('.java', '.kt'))]
        if nontarget_files:
            yield emit_message("")
            yield self.emit_separator()
            yield self.emit_phase_header(3, "📄 비타겟 파일 분석", f"{len(nontarget_files)}개 파일")
            yield self.emit_separator()

            async for chunk in run_phase_nontarget(
                analyzer=self,
                file_names=nontarget_files,
                client=client,
                orchestrator=orchestrator,
                stats=stats,
            ):
                yield chunk

            yield emit_message("")
            yield self.emit_phase_complete(3, "비타겟 파일 분석 완료")
        else:
            yield emit_message("")
            yield emit_message("   ℹ️ 비타겟 파일 없음 (Java/Kotlin 파일만 존재)")

        if not await pipeline_state.wait_if_paused():
            yield emit_message("⏹️ 파이프라인이 중단되었습니다")
            pipeline_state.set_phase(PipelinePhase.CANCELLED)
            return

        pipeline_state.set_phase(PipelinePhase.COMPLETED)

    async def _load_all_files(
        self,
        file_names: list[tuple[str, str]],
        orchestrator: Any,
    ) -> List[FileAnalysisContext]:
        """모든 파일의 AST JSON을 병렬로 로드"""
        async def load_single(directory: str, file_name: str) -> FileAnalysisContext:
            base_name = os.path.splitext(file_name)[0]
            ast_path = os.path.join(orchestrator.dirs["analysis"], "target", directory, f"{base_name}.json")
            try:
                ast_content, encoding_used = await read_text_with_fallback(
                    ast_path, preferred_encodings=("utf-8", "utf-8-sig", "cp949", "euc-kr")
                )
                if encoding_used != "utf-8":
                    logging.info("[FILE_ENCODING] AST JSON fallback decoding: %s (encoding=%s)", ast_path, encoding_used)
                return FileAnalysisContext(directory=directory, file_name=file_name, ast_data=json.loads(ast_content))
            except FileNotFoundError:
                raise RuntimeError(
                    f"ANTLR JSON 파일을 찾을 수 없습니다: {ast_path}\n"
                    "ANTLR 파싱이 실패했거나 파일이 생성되지 않았습니다."
                )
            except json.JSONDecodeError as e:
                raise RuntimeError(
                    f"ANTLR JSON 파싱 오류 ({file_name}): {e}\n"
                    "ANTLR 파서가 올바른 JSON을 생성하지 못했습니다."
                )
        return await asyncio.gather(*[load_single(d, f) for d, f in file_names])

    async def build_user_story_doc(
        self,
        client: Neo4jClient,
        orchestrator: Any,
    ) -> Optional[str]:
        """분석된 클래스/인터페이스에서 User Story 문서 생성"""
        query = """
            MATCH (__cy_n__)
            WHERE (__cy_n__:CLASS OR __cy_n__:INTERFACE)
              AND __cy_n__.summary IS NOT NULL
            OPTIONAL MATCH (__cy_n__)-[:HAS_USER_STORY]->(__cy_us__:UserStory)
            OPTIONAL MATCH (__cy_us__)-[:HAS_AC]->(__cy_ac__:AcceptanceCriteria)
            WITH __cy_n__, __cy_us__, collect(DISTINCT {
                id: __cy_ac__.id,
                title: __cy_ac__.title,
                given: __cy_ac__.given,
                when: __cy_ac__.when,
                then: __cy_ac__.then
            }) AS acceptance_criteria
            WITH __cy_n__, collect(DISTINCT {
                id: __cy_us__.id,
                role: __cy_us__.role,
                goal: __cy_us__.goal,
                benefit: __cy_us__.benefit,
                acceptance_criteria: acceptance_criteria
            }) AS user_stories
            RETURN __cy_n__.class_name AS name, 
                   __cy_n__.summary AS summary,
                   user_stories AS user_stories, 
                   labels(__cy_n__)[0] AS type
            ORDER BY __cy_n__.file_name, __cy_n__.start_line
        """

        async with self._cypher_lock:
            results = await client.execute_queries([query])

        if not results or not results[0]:
            log_process(
                "ANALYZE", "USER_STORY",
                "User Story 생성 스킵: 분석된 클래스/인터페이스가 없습니다",
                logging.INFO
            )
            return None

        filtered = [
            r for r in results[0]
            if r.get("summary") or (r.get("user_stories") and len(r["user_stories"]) > 0)
        ]

        if not filtered:
            return None

        log_process("ANALYZE", "USER_STORY", f"User Story 생성 | 대상={len(filtered)}개 클래스")
        return generate_user_story_document(
            results=filtered,
            source_name="ROBO",
            source_type="Java 클래스/인터페이스",
        )


FrameworkAnalyzer = FrameworkPipeline
