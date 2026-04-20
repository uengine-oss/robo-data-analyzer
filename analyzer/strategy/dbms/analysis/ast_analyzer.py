"""AST 프로세서 - BaseAstProcessor + DbmsAstProcessor 통합

BaseAstProcessor: 공통 AST 처리 파이프라인 (템플릿 메서드 패턴)
DbmsAstProcessor: DBMS 전용 AST 처리 및 LLM 분석

파이프라인:
- Phase 1: build_static_graph_queries() - 정적 그래프 쿼리 생성
- Phase 1.5: _generate_node_contexts() - 노드 컨텍스트 생성 (부모 + 이전 형제)
- Phase 2: run_llm_analysis() - LLM 분석

전략별 구현 필요:
- _collect_nodes(): AST 수집
- _invoke_llm(): LLM 호출
- _build_analysis_queries(): 분석 결과 쿼리 변환
- _process_unit_summaries(): 단위 요약 처리

리팩토링: BaseAstProcessor 상속으로 공통 로직 재사용
"""

from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, Tuple, Set, Union

from util.logger import log_process
from util.code_text_utils import parse_table_identifier
from analyzer.common.pipeline_control import pipeline_controller

from analyzer.strategy.dbms.common.node_models import StatementNode, AnalysisBatch, BatchPlanner, ProcedureInfo
from analyzer.strategy.dbms.common.analysis_constants import (
    PROCEDURE_TYPES,
    TABLE_RELATIONSHIP_MAP,
    MAX_CONCURRENCY,
    MAX_CONTEXT_TOKEN,
    DBMS_SYSTEM_PACKAGES,
)
from analyzer.strategy.dbms.common.naming_helpers import build_statement_name
from util.llm_prompt_handler import prompts
from analyzer.strategy.dbms.analysis.ast_collector import StatementCollector


# =========================================================================
# BaseAstProcessor - 공통 AST 처리 파이프라인
# =========================================================================

class BaseAstProcessor(ABC):
    """AST 처리 및 LLM 분석 공통 파이프라인

    2단계 분석 지원:
    - Phase 1: build_static_graph_queries() - 정적 그래프 쿼리 생성
    - Phase 1.5: _generate_node_contexts() - 노드 컨텍스트 생성 (부모 + 이전 형제)
    - Phase 2: run_llm_analysis() - LLM 분석 후 업데이트 쿼리 생성

    전략별로 오버라이드해야 하는 메서드:
    - _collect_nodes(): AST 수집
    - _build_static_node_queries(): 정적 노드 쿼리 생성
    - _build_relationship_queries(): 관계 쿼리 생성
    - _invoke_llm(): LLM 호출
    - _build_analysis_queries(): 분석 결과 쿼리 변환
    - _process_unit_summaries(): 단위 요약 처리
    - _extract_node_context(): 노드 컨텍스트 추출 (부모 + 이전 형제)
    - _get_excluded_context_types(): 컨텍스트 생성 제외 타입
    - _get_unit_info_dict(): 단위(프로시저/클래스) 정보 딕셔너리
    """

    def __init__(
        self,
        antlr_data: dict,
        directory: str,
        file_name: str,
        api_key: str,
        locale: str,
        last_line: int,
        source_content: str = "",
    ):
        """공통 초기화

        source_content: 원본 소스 파일 내용. AST JSON에 code 속성이 없을 때
        startLine/endLine으로 코드를 추출하는 fallback으로 사용.
        """
        self.antlr_data = antlr_data
        self.last_line = last_line
        self.source_content = source_content

        # Windows 경로 구분자(\\)를 /로 변환하여 일관성 유지
        normalized_dir = directory.replace('\\', '/') if directory else ''
        self.directory = normalized_dir
        self.file_name = file_name
        self.api_key = api_key
        self.locale = locale

        # full_directory: 디렉토리 + 파일명 (Neo4j directory 속성으로 사용)
        self.full_directory = f"{normalized_dir}/{file_name}" if normalized_dir else file_name

        self.max_workers = MAX_CONCURRENCY
        self.file_last_line = last_line

        # AST 수집 결과 캐시 (Phase 1에서 수집, Phase 2에서 사용)
        self._nodes: Optional[List[StatementNode]] = None
        self._unit_info: Optional[Dict[str, Any]] = None  # 프로시저/클래스 정보

    # =========================================================================
    # 추상 메서드 - 전략별 구현 필요
    # =========================================================================

    @abstractmethod
    def _collect_nodes(self) -> Tuple[List[StatementNode], Dict[str, Any]]:
        """AST를 수집하여 노드 리스트와 단위 정보를 반환합니다.

        Returns:
            (노드 리스트, 단위 정보 딕셔너리)
            - DBMS: (nodes, procedures)
            - Framework: (nodes, classes)
        """
        raise NotImplementedError

    @abstractmethod
    def _build_static_node_queries(self, node: StatementNode) -> List[Union[str, Dict[str, Any]]]:
        """정적 노드 생성 쿼리 리스트를 반환합니다 (파라미터화된 Dict 포함)."""
        raise NotImplementedError

    @abstractmethod
    def _build_relationship_queries(self) -> List[Union[str, Dict[str, Any]]]:
        """정적 관계 쿼리를 생성합니다 (파라미터화된 Dict 포함)."""
        raise NotImplementedError

    @abstractmethod
    async def _invoke_llm(self, batch: AnalysisBatch) -> Any:
        """LLM을 호출하여 배치 분석 결과를 반환합니다.

        Returns:
            LLM 분석 결과 (전략별 형식)
        """
        raise NotImplementedError

    @abstractmethod
    def _build_analysis_queries(
        self,
        batch: AnalysisBatch,
        llm_result: Any,
        unit_summary_store: Optional[Dict[str, Dict[str, str]]],
    ) -> List[Union[str, Dict[str, Any]]]:
        """LLM 분석 결과를 쿼리로 변환합니다.

        Args:
            batch: 분석 배치
            llm_result: LLM 분석 결과
            unit_summary_store: 단위별 summary 저장소 (프로시저/클래스)

        Returns:
            분석 결과 업데이트 쿼리 리스트 (파라미터화된 Dict 포함)
        """
        raise NotImplementedError


    @abstractmethod
    async def _extract_node_context(
        self,
        skeleton_code: str,
        ancestor_context: str,
        sibling_code: str,
    ) -> Tuple[str, str]:
        """부모 스켈레톤 + 이전 형제 코드에서 컨텍스트를 추출합니다.

        Args:
            skeleton_code: 자식 구간이 .... 로 압축된 부모 코드
            ancestor_context: 조상 노드들의 누적 컨텍스트
            sibling_code: 이전 형제 노드의 원문 코드 (없으면 빈 문자열)

        Returns:
            (parent_context, sibling_context) 튜플
        """
        raise NotImplementedError

    @abstractmethod
    def _get_excluded_context_types(self) -> Set[str]:
        """컨텍스트 생성에서 제외할 노드 타입을 반환합니다.

        Returns:
            제외할 노드 타입 Set
            - DBMS: PROCEDURE_TYPES
            - Framework: CLASS_TYPES
        """
        raise NotImplementedError

    def _use_dml_ranges(self) -> bool:
        """배치 계획 시 DML 범위를 포함할지 여부를 반환합니다.

        기본값: False (DML 분석 불필요)
        DBMS에서만 True로 오버라이드합니다.

        Returns:
            True: DML 분석 필요 (DBMS)
            False: DML 분석 불필요 (기본값)
        """
        return False

    async def _run_preprocessing(self) -> List[Union[str, Dict[str, Any]]]:
        """선행 처리 (DBMS에서 오버라이드). 기본은 빈 리스트."""
        return []

    # =========================================================================
    # Phase 1: 정적 그래프 쿼리 생성
    # =========================================================================

    def build_static_graph_queries(self) -> List[Union[str, Dict[str, Any]]]:
        """[Phase 1] AST를 수집하고 정적 그래프 쿼리를 생성합니다.

        Returns:
            정적 노드 및 관계 생성 쿼리 리스트 (문자열 또는 파라미터화된 Dict)
        """
        log_process("PHASE:1", "AST", f"🏗️ {self.full_directory} 정적 그래프 생성")

        # AST 수집
        self._nodes, self._unit_info = self._collect_nodes()

        if not self._nodes:
            log_process("PHASE:1", "AST", f"⚠️ {self.full_directory}: 분석 대상 노드 없음")
            return []

        # Package 노드 생성 + FILE → Package 관계
        queries: List[Union[str, Dict[str, Any]]] = []
        queries.extend(self._build_package_queries())

        # 정적 노드 쿼리 생성 (파라미터화된 Dict 쿼리도 포함)
        for node in self._nodes:
            queries.extend(self._build_static_node_queries(node))

        # 관계 쿼리 생성
        queries.extend(self._build_relationship_queries())

        log_process("PHASE:1", "AST", f"✅ {self.full_directory}: {len(queries)}개 쿼리 생성")
        return queries

    # =========================================================================
    # Phase 1.5: 노드 컨텍스트 생성 (부모 + 이전 형제)
    # =========================================================================

    async def _generate_node_contexts(self) -> None:
        """부모 노드들의 컨텍스트를 Top-down 순서로 생성합니다.

        처리 흐름:
        1. 부모 노드들을 깊이 순으로 정렬 (얕은 것 먼저)
        2. 각 부모에 대해 스켈레톤 + 이전 형제 원문 + 조상 컨텍스트 → LLM → 컨텍스트 추출
        3. 추출된 parent_context와 sibling_context를 노드에 저장
        4. context_ready_event 설정
        """
        if not self._nodes:
            return

        excluded_types = self._get_excluded_context_types()

        # 컨텍스트 생성이 필요한 부모 노드들 수집
        parent_nodes = [
            node for node in self._nodes
            if node.needs_context_generation(excluded_types)
        ]

        if not parent_nodes:
            log_process("PHASE:1", "CONTEXT", "⏭️ 컨텍스트 생성이 필요한 부모 노드 없음")
            # 모든 노드의 context_ready_event 설정
            for node in self._nodes:
                node.context_ready_event.set()
            return

        # 깊이 계산 함수
        def get_depth(node: StatementNode) -> int:
            depth = 0
            current = node.parent
            while current:
                depth += 1
                current = current.parent
            return depth

        # 깊이 순으로 정렬 (얕은 것 먼저 → Top-down 보장)
        parent_nodes.sort(key=get_depth)

        log_process("PHASE:1", "CONTEXT", f"🔄 노드 컨텍스트 생성 시작: {len(parent_nodes)}개 노드")

        # 순차적으로 처리 (깊이 순서 보장)
        # 같은 깊이의 노드들은 병렬 처리 가능
        current_depth = -1
        current_batch: List[StatementNode] = []

        async def process_one(node: StatementNode) -> None:
            """한 노드의 컨텍스트를 생성 (부모 + 형제 요약 LLM).

            부모 context_ready_event 를 기다려 상위 컨텍스트를 확보한 후 LLM 호출.
            실패 시 예외 전파 (Fail-Fast) + context_ready_event 는 무조건 set 해 데드락 방지.
            """
            try:
                if node.parent and node.parent.needs_context_generation(excluded_types):
                    await node.parent.context_ready_event.wait()

                skeleton = node.get_skeleton_code()
                ancestor_ctx = node.get_ancestor_context(MAX_CONTEXT_TOKEN)
                sibling_code = node.get_sibling_code()
                parent_ctx, sibling_ctx = await self._extract_node_context(
                    skeleton, ancestor_ctx, sibling_code,
                )

                node.context = parent_ctx
                node.sibling_context = sibling_ctx
                log_process("PHASE:1", "CONTEXT", f"✅ 컨텍스트 생성 완료: {node.node_type}[{node.start_line}~{node.end_line}]")
            except Exception as e:
                log_process("PHASE:1", "CONTEXT", f"❌ 컨텍스트 생성 실패 (치명적): {node.node_type}[{node.start_line}]: {e}", logging.ERROR)
                raise
            finally:
                node.context_ready_event.set()

        async def process_context_batch(batch: List[StatementNode]) -> None:
            """같은 깊이의 노드를 AsyncBatchExecutor 로 continuous 병렬 처리.

            깊이 간 순서는 외부 루프가 순차로 호출 (부모 깊이 context_ready_event
            가 모두 set 된 후에야 자식 깊이가 시작됨).
            """
            from util.llm_rate_limiter import get_executor
            await get_executor().run_batch(
                items=batch,
                processor=process_one,
                label="DBMS_NODE_CONTEXT",
            )

        # 깊이별로 배치 처리
        for node in parent_nodes:
            depth = get_depth(node)
            if depth != current_depth:
                # 이전 깊이 배치 처리
                if current_batch:
                    await process_context_batch(current_batch)
                current_depth = depth
                current_batch = [node]
            else:
                current_batch.append(node)

        # 마지막 배치 처리
        if current_batch:
            await process_context_batch(current_batch)

        # 컨텍스트 생성 불필요한 노드들도 context_ready_event 설정
        for node in self._nodes:
            if not node.context_ready_event.is_set():
                node.context_ready_event.set()

        log_process("PHASE:1", "CONTEXT", f"✅ 노드 컨텍스트 생성 완료: {len(parent_nodes)}개")

    # =========================================================================
    # Phase 2: LLM 분석
    # =========================================================================

    async def run_llm_analysis(
        self,
        on_queries_ready=None,
    ) -> Tuple[List[Union[str, Dict[str, Any]]], int, List[Dict[str, Any]], Dict[str, Dict[str, str]]]:
        """[Phase 2] LLM 분석을 실행하고 배치별로 즉시 쿼리를 전달합니다.

        중요: 자식→부모 요약 의존성을 보장하기 위해 completion_event 기반 대기
        - 부모 노드는 자식 노드의 completion_event를 기다린 후 실행
        - leaf 노드는 바로 실행, parent 노드는 자식 완료 후 실행

        Args:
            on_queries_ready: 배치별 쿼리 즉시 전달 콜백 (선택).
                async def on_queries_ready(queries, batch_id, total_batches)
                호출 시 즉시 Neo4j 저장 + 프론트 스트리밍 수행.

        Returns:
            (분석 결과 업데이트 쿼리 리스트, 실패한 배치 수, 실패 상세 정보 리스트, unit_summary_store)
        """
        if self._nodes is None:
            raise RuntimeError(f"Phase 1이 먼저 실행되어야 합니다: {self.file_name}")

        log_process("ANALYZE", "PHASE2", f"🤖 {self.full_directory} LLM 분석 시작")

        all_queries: List[Union[str, Dict[str, Any]]] = []
        failed_batch_count = 0
        all_failed_details: List[Dict[str, Any]] = []

        await self._generate_node_contexts()

        # 배치 분석
        planner = BatchPlanner()
        batches = planner.plan(self._nodes, include_dml_ranges=self._use_dml_ranges())

        if not batches:
            log_process("ANALYZE", "PHASE2", f"⚠️ {self.full_directory}: 분석 대상 배치 없음")
            return all_queries, 0, [], {}

        log_process("ANALYZE", "PHASE2", f"📊 배치 {len(batches)}개 (completion_event 기반 의존성 보장)")

        # 단위별 summary 수집용 저장소 (배치 처리 전에 초기화)
        unit_summary_store: Dict[str, Dict[str, str]] = {
            key: {} for key in (self._unit_info or {})
        }

        async def process_batch(batch: AnalysisBatch) -> List[str]:
            """배치 처리 후 쿼리 반환. 노드 summary 설정 + Neo4j 스트림.

            핵심: 부모 노드는 자식 completion_event 를 기다린 후 실행됨
            → 깊이 계산 없이 자연스럽게 leaf → parent 순서 보장.
            실패 시 노드 ok=False 마킹 + completion_event.set() 로 데드락 방지.
            rate limit / 동시성은 AsyncBatchExecutor + get_rate_limiter() 가 관리.
            """
            if not await pipeline_controller.check_continue():
                raise RuntimeError("파이프라인 중단됨")
            try:
                # 1. 배치 내 모든 노드의 자식 완료 + 부모 컨텍스트 대기
                for node in batch.nodes:
                    if node.parent:
                        await node.parent.context_ready_event.wait()
                    if node.has_children:
                        for child in node.children:
                            await child.completion_event.wait()
                            if not child.ok:
                                node.ok = False

                log_process("ANALYZE", "LLM", f"배치 #{batch.batch_id} 처리 중 ({len(batch.nodes)}개 노드)")
                llm_result = await self._invoke_llm(batch)
                self._apply_summary_to_nodes(batch, llm_result)
                queries = self._build_analysis_queries(batch, llm_result, unit_summary_store)
                if on_queries_ready and queries:
                    await on_queries_ready(queries, batch.batch_id, len(batches))
                return queries
            except Exception:
                for node in batch.nodes:
                    node.ok = False
                raise
            finally:
                for node in batch.nodes:
                    node.completion_event.set()

        # 모든 배치를 AsyncBatchExecutor 로 continuous 병렬 실행
        # (completion_event 로 부모-자식 순서 보장, rate limiter 싱글톤으로 동시성 제어)
        from util.llm_rate_limiter import get_executor
        batch_results = await get_executor().run_batch(
            items=batches,
            processor=process_batch,
            label=f"DBMS_AST_BATCH:{self.full_directory}",
        )

        # 결과 병합: 성공한 배치의 쿼리 수집, 실패 배치는 상세 수집
        for i, queries in enumerate(batch_results):
            if queries is None:
                # AsyncBatchExecutor 는 항목 실패 시 None 반환 + 에러 로그
                batch = batches[i]
                all_failed_details.append({
                    "batch_id": batch.batch_id,
                    "node_ranges": ", ".join(f"L{n.start_line}-{n.end_line}" for n in batch.nodes),
                    "error": "배치 처리 실패 (상세는 BATCH 로그 참조)",
                })
                failed_batch_count += 1
            else:
                all_queries.extend(queries)

        # 배치 실패 시 즉시 중단 — 부분 실패 허용 안 함
        if failed_batch_count > 0:
            raise RuntimeError(f"{self.full_directory}: {failed_batch_count}개 배치 실패 (상세: {all_failed_details})")

        log_process("ANALYZE", "PHASE2", f"✅ {self.full_directory}: {len(all_queries)}개 업데이트 쿼리")
        return all_queries, failed_batch_count, all_failed_details, unit_summary_store

    def _apply_summary_to_nodes(self, batch: AnalysisBatch, llm_result: Any) -> None:
        """LLM 결과에서 summary를 추출하여 노드에 적용합니다.

        기본 구현: llm_result가 dict이고 'analysis' 배열이 있는 경우 처리
        전략별로 오버라이드 가능

        Raises:
            RuntimeError: llm_result가 None이거나 예상치 못한 타입일 때
        """
        if not llm_result:
            raise RuntimeError(f"배치#{batch.batch_id} LLM 결과 없음")

        def set_node_from_analysis(node, analysis: dict) -> None:
            if not analysis:
                return
            node.summary = analysis.get("summary") or ""

        # 일반적인 경우: llm_result가 dict이고 analysis 배열이 있음
        if isinstance(llm_result, dict):
            analysis_list = llm_result.get("analysis") or []
            for node, analysis in zip(batch.nodes, analysis_list):
                set_node_from_analysis(node, analysis or {})
        # DBMS의 경우: (general_result, table_result) 튜플
        elif isinstance(llm_result, tuple) and len(llm_result) >= 1:
            general_result = llm_result[0]
            if general_result:
                analysis_list = general_result.get("analysis") or []
                for node, analysis in zip(batch.nodes, analysis_list):
                    set_node_from_analysis(node, analysis or {})
        else:
            raise RuntimeError(f"배치#{batch.batch_id} 알 수 없는 결과 타입: {type(llm_result).__name__}")

    # =========================================================================
    # 유틸리티 메서드
    # =========================================================================

    @staticmethod
    def validate_dict_result(
        result: Any,
        context: str,
        batch_id: Optional[int] = None,
        allow_none: bool = False,
    ) -> Dict[str, Any]:
        """LLM 결과가 dict인지 검증하고, 아니면 예외를 발생시킵니다.

        Args:
            result: 검증할 결과
            context: 로그에 표시할 컨텍스트 (예: "청크 분석", "User Story")
            batch_id: 배치 ID (있으면 로그에 포함)
            allow_none: True면 None일 때 빈 dict 반환, False면 예외 발생

        Returns:
            result가 dict이면 그대로 반환

        Raises:
            RuntimeError: result가 dict가 아닐 때
        """
        if result is None:
            if allow_none:
                return {}
            batch_info = f"배치#{batch_id} " if batch_id else ""
            raise RuntimeError(f"{batch_info}{context} 결과가 None입니다")

        if isinstance(result, dict):
            return result

        batch_info = f"배치#{batch_id} " if batch_id else ""
        raise RuntimeError(f"{batch_info}{context} 결과가 dict가 아님: {type(result).__name__}")

    # =========================================================================
    # Package 노드 및 관계 쿼리
    # =========================================================================

    def _build_package_queries(self) -> List[Dict[str, Any]]:
        """Package 노드 생성 + FILE -[BELONGS_TO_PACKAGE]-> Package 관계"""
        queries: List[Dict[str, Any]] = []
        package_name = self.directory  # 폴더명 (예: RWIS)

        if not package_name:
            return queries

        # Package 노드 MERGE
        queries.append({
            "query": (
                "MERGE (__cy_pkg__:PACKAGE {name: $package_name})\n"
                "RETURN __cy_pkg__"
            ),
            "params": {"package_name": package_name}
        })

        # FILE -[BELONGS_TO_PACKAGE]-> Package
        queries.append({
            "query": (
                "MATCH (__cy_f__:FILE {directory: $directory, file_name: $file_name})\n"
                "MATCH (__cy_pkg__:PACKAGE {name: $package_name})\n"
                "MERGE (__cy_f__)-[__cy_r__:BELONGS_TO_PACKAGE]->(__cy_pkg__)\n"
                "RETURN __cy_f__, __cy_pkg__, __cy_r__"
            ),
            "params": {
                "directory": self.full_directory,
                "file_name": self.file_name,
                "package_name": package_name,
            }
        })

        return queries

    # =========================================================================
    # 공통 관계 쿼리 빌더 (파라미터화)
    # =========================================================================

    def _build_has_procedure_query(self, parent: "StatementNode", child: "StatementNode") -> Dict[str, Any]:
        """HAS_PROCEDURE 관계 쿼리 (FILE → PROCEDURE/FUNCTION)"""
        return {
            "query": (
                f"MATCH (__cy_p__:{parent.node_type} {{startLine: $parent_line, directory: $directory, file_name: $file_name}})\n"
                f"MATCH (__cy_c__:{child.node_type} {{startLine: $child_line, directory: $directory, file_name: $file_name}})\n"
            f"MERGE (__cy_p__)-[__cy_r__:HAS_PROCEDURE]->(__cy_c__)\n"
            f"RETURN __cy_r__"
            ),
            "params": {
                "parent_line": parent.start_line,
                "child_line": child.start_line,
                "directory": self.full_directory,
                "file_name": self.file_name,
            }
        }

    def _build_parent_of_query(self, parent: "StatementNode", child: "StatementNode") -> Dict[str, Any]:
        """PARENT_OF 관계 쿼리 (파라미터화)"""
        return {
            "query": (
                f"MATCH (__cy_p__:{parent.node_type} {{startLine: $parent_line, directory: $directory, file_name: $file_name}})\n"
                f"MATCH (__cy_c__:{child.node_type} {{startLine: $child_line, directory: $directory, file_name: $file_name}})\n"
            f"MERGE (__cy_p__)-[__cy_r__:PARENT_OF]->(__cy_c__)\n"
            f"RETURN __cy_r__"
            ),
            "params": {
                "parent_line": parent.start_line,
                "child_line": child.start_line,
                "directory": self.full_directory,
                "file_name": self.file_name,
            }
        }


# =========================================================================
# DbmsAstProcessor - DBMS 전용 AST 처리
# =========================================================================

class DbmsAstProcessor(BaseAstProcessor):
    """DBMS AST 처리 및 LLM 분석 파이프라인"""

    def __init__(
        self,
        antlr_data: dict,
        directory: str,
        file_name: str,
        api_key: str,
        locale: str,
        dbms: str,
        last_line: int,
        default_schema: str = "public",
        ddl_table_metadata: Optional[Dict[Tuple[str, str], Dict[str, Any]]] = None,
        name_case: str = "original",
        source_content: str = "",
    ):
        super().__init__(
            antlr_data=antlr_data,
            directory=directory,
            file_name=file_name,
            api_key=api_key,
            locale=locale,
            last_line=last_line,
            source_content=source_content,
        )

        self.dbms = (dbms or 'postgres').lower()
        self.default_schema = default_schema
        self._ddl_table_metadata = ddl_table_metadata or {}
        self.name_case = (name_case or 'original').lower()

        self._table_summary_store: Dict[Tuple[str, str], Dict[str, Any]] = {}
        self._variable_roles: Dict[str, Dict[str, List[str]]] = {}

    def _apply_name_case(self, name: str) -> str:
        """대소문자 변환 적용"""
        if not name:
            return name
        if self.name_case == "uppercase":
            return name.upper()
        elif self.name_case == "lowercase":
            return name.lower()
        return name

    def _get_effective_schema(self, schema: Optional[str]) -> str:
        """스키마가 없으면 default_schema 사용"""
        return schema if schema else (self.default_schema if self.default_schema else 'public')

    def _get_ddl_metadata(self, schema: Optional[str], table_name: str) -> Tuple[Dict[str, Any], bool]:
        """DDL 메타데이터 조회 및 대소문자 변환 여부 결정"""
        effective_schema = self._get_effective_schema(schema)
        ddl_lookup_key = (
            (effective_schema.lower() if effective_schema else 'public'),
            table_name.lower()
        )
        ddl_meta = self._ddl_table_metadata.get(ddl_lookup_key, {})
        skip_case_conversion = bool(ddl_meta and ddl_meta.get('original_name'))
        return ddl_meta, skip_case_conversion

    def _resolve_table_name_with_ddl(self, schema_raw: Optional[str], name_raw: str) -> Tuple[str, str, bool]:
        """DDL 캐시를 고려하여 테이블명 정규화"""
        ddl_meta, skip_case_conversion = self._get_ddl_metadata(schema_raw, name_raw)

        if skip_case_conversion:
            effective_schema = self._get_effective_schema(schema_raw)
            schema_part = ddl_meta.get('original_schema', self._apply_name_case(effective_schema))
            name_part = ddl_meta.get('original_name')
        else:
            schema_part = self._apply_name_case(schema_raw) if schema_raw else self._apply_name_case(self.default_schema)
            name_part = self._apply_name_case(name_raw)

        return schema_part, name_part, skip_case_conversion

    def _resolve_column_name_with_ddl(self, column_name_raw: str, ddl_columns: Dict[str, Any]) -> str:
        """DDL 캐시를 고려하여 컬럼명 정규화"""
        ddl_col_meta = ddl_columns.get(column_name_raw.upper() if self.name_case == 'uppercase' else column_name_raw)
        if ddl_col_meta is not None:
            return column_name_raw.upper() if self.name_case == 'uppercase' else column_name_raw

        for ddl_col_name in ddl_columns.keys():
            if ddl_col_name.lower() == column_name_raw.lower():
                return ddl_col_name

        return self._apply_name_case(column_name_raw)

    # =========================================================================
    # BaseAstProcessor 추상 메서드 구현
    # =========================================================================

    def _collect_nodes(self) -> Tuple[List[StatementNode], Dict[str, ProcedureInfo]]:
        """AST 수집"""
        collector = StatementCollector(
            self.antlr_data, self.directory, self.file_name,
            source_content=getattr(self, "source_content", "") or "",
        )
        return collector.collect()

    def _get_excluded_context_types(self) -> Set[str]:
        """컨텍스트 생성에서 제외할 노드 타입"""
        return PROCEDURE_TYPES

    def _use_dml_ranges(self) -> bool:
        """DBMS는 DML 범위 포함"""
        return True

    async def _extract_node_context(
        self,
        skeleton_code: str,
        ancestor_context: str,
        sibling_code: str,
    ) -> Tuple[str, str]:
        """부모 + 이전 형제 컨텍스트 추출 → rules/dbms/node_context.yaml"""
        def _call():
            result = prompts.execute_raw(
                "dbms", "node_context",
                {"skeleton_code": skeleton_code, "ancestor_context": ancestor_context,
                 "sibling_code": sibling_code, "locale": self.locale},
                self.api_key,
            )
            if not isinstance(result, dict):
                raise ValueError(f"node_context 규칙이 dict가 아닌 값을 반환: {type(result)}")
            parent_ctx = result.get("parent_context", "").strip()
            sibling_ctx = result.get("sibling_context", "").strip()
            if not parent_ctx:
                raise ValueError("node_context 규칙이 빈 parent_context를 반환")
            return parent_ctx, sibling_ctx
        return await asyncio.to_thread(_call)

    def _build_static_node_queries(self, node: StatementNode) -> List[Union[str, Dict[str, Any]]]:
        """정적 노드 생성 쿼리 생성"""
        queries: List[Union[str, Dict[str, Any]]] = []
        label = node.node_type

        # --- 이름 결정 ---
        if label == "FILE":
            node_name = self.file_name
        elif label in PROCEDURE_TYPES and node.unit_name:
            node_name = node.unit_name
        else:
            node_name = f"{label}[{node.start_line}]"

        # --- 파라미터 구성 ---
        params: Dict[str, Any] = {
            "start_line": node.start_line,
            "end_line": node.end_line,
            "directory": self.full_directory,
            "file_name": self.file_name,
            "node_name": node_name,
            "node_code": node.code,
            "token": node.token,
            "has_children": node.has_children,
        }

        set_parts = [
            "__cy_n__.endLine = $end_line",
            "__cy_n__.name = $node_name",
            "__cy_n__.node_code = $node_code",
            "__cy_n__.token = $token",
            "__cy_n__.has_children = $has_children",
        ]

        if node.signature:
            set_parts.append("__cy_n__.signature = $signature")
            params["signature"] = node.signature
        if node.parameters:
            set_parts.append("__cy_n__.parameters = $parameters")
            params["parameters"] = node.parameters

        if label in PROCEDURE_TYPES and node.unit_name:
            set_parts.append("__cy_n__.procedure_name = $procedure_name")
            set_parts.append("__cy_n__.procedure_type = $procedure_type")
            params["procedure_name"] = node.unit_name
            params["procedure_type"] = label
            if node.schema_name:
                set_parts.append("__cy_n__.schema_name = $schema_name")
                params["schema_name"] = node.schema_name
        elif node.unit_name:
            set_parts.append("__cy_n__.procedure_name = $procedure_name")
            params["procedure_name"] = node.unit_name
            if node.schema_name:
                set_parts.append("__cy_n__.schema_name = $schema_name")
                params["schema_name"] = node.schema_name

        if node.has_children:
            set_parts.append("__cy_n__.summarized_code = $summarized_code")
            params["summarized_code"] = node.get_placeholder_code()

        set_clause = ", ".join(set_parts)

        # --- 쿼리 생성 ---
        if label in PROCEDURE_TYPES and node.unit_name:
            if node.schema_name:
                query = (
                    f"MERGE (__cy_n__:{label} {{schema_name: $schema_name, procedure_name: $procedure_name, directory: $directory, file_name: $file_name}})\n"
                    f"SET __cy_n__.startLine = $start_line, {set_clause}\n"
                    f"RETURN __cy_n__"
                )
            else:
                query = (
                    f"MERGE (__cy_n__:{label} {{procedure_name: $procedure_name, directory: $directory, file_name: $file_name}})\n"
                    f"SET __cy_n__.startLine = $start_line, {set_clause}\n"
                    f"RETURN __cy_n__"
                )
            queries.append({"query": query, "params": params})
        else:
            query = (
                f"MERGE (__cy_n__:{label} {{startLine: $start_line, directory: $directory, file_name: $file_name}})\n"
                f"SET {set_clause}\n"
                f"RETURN __cy_n__"
            )
            queries.append({"query": query, "params": params})
        return queries

    def _build_relationship_queries(self) -> List[Union[str, Dict[str, Any]]]:
        """정적 관계 쿼리 생성 (HAS_PROCEDURE, PARENT_OF, NEXT)"""
        queries: List[Union[str, Dict[str, Any]]] = []

        for node in self._nodes or []:
            for child in node.children:
                if node.node_type == "FILE" and child.node_type in PROCEDURE_TYPES:
                    queries.append(self._build_has_procedure_query(node, child))
                else:
                    queries.append(self._build_parent_of_query(node, child))

            prev = None
            for child in node.children:
                if prev:
                    queries.append(self._build_next_query(prev, child))
                prev = child

        return queries

    def _build_next_query(self, prev: StatementNode, current: StatementNode) -> Dict[str, Any]:
        """NEXT 관계 쿼리 생성"""
        return {
            "query": (
                f"MATCH (__cy_prev__:{prev.node_type} {{startLine: $prev_line, directory: $directory, file_name: $file_name}})\n"
                f"MATCH (__cy_curr__:{current.node_type} {{startLine: $curr_line, directory: $directory, file_name: $file_name}})\n"
            f"MERGE (__cy_prev__)-[__cy_r__:NEXT]->(__cy_curr__)\n"
            f"RETURN __cy_r__"
            ),
            "params": {
                "prev_line": prev.start_line,
                "curr_line": current.start_line,
                "directory": self.full_directory,
                "file_name": self.file_name,
            }
        }

    async def _invoke_llm(self, batch: AnalysisBatch) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
        """LLM 호출 (일반 분석 + DML 분석)"""
        general_task = None
        if batch.ranges:
            code, context = batch.build_payload()
            def _call_analyze_code():
                inputs = {"code": code, "ranges": batch.ranges, "count": len(batch.ranges), "locale": self.locale}
                if context.strip():
                    inputs["context"] = context
                return prompts.execute_raw("dbms", "node_analysis", inputs, self.api_key)
            general_task = asyncio.to_thread(_call_analyze_code)

        table_task = None
        dml_payload = batch.build_dml_payload()
        if dml_payload and batch.dml_ranges:
            code, context = dml_payload
            def _call_analyze_dml():
                inputs = {"code": code, "ranges": batch.dml_ranges, "locale": self.locale}
                if context.strip():
                    inputs["context"] = context
                return prompts.execute_raw("dbms", "dml_extract", inputs, self.api_key)
            table_task = asyncio.to_thread(_call_analyze_dml)

        if general_task and table_task:
            results = await asyncio.gather(general_task, table_task)
            return tuple(results)
        elif general_task:
            return await general_task, None
        elif table_task:
            return None, await table_task
        else:
            raise RuntimeError("LLM 분석 대상이 없습니다")

    def _build_analysis_queries(
        self,
        batch: AnalysisBatch,
        llm_result: Any,
        unit_summary_store: Optional[Dict[str, Dict[str, str]]],
    ) -> List[Union[str, Dict[str, Any]]]:
        """LLM 분석 결과를 쿼리로 변환"""
        queries: List[Union[str, Dict[str, Any]]] = []

        # --- 결과 검증 ---
        if not isinstance(llm_result, tuple):
            raise RuntimeError(f"배치#{batch.batch_id} llm_result가 tuple이 아님: {type(llm_result).__name__}")

        general_result, table_result = llm_result

        # --- Summary 처리 ---
        general_result = self.validate_dict_result(
            general_result, "general_result", batch.batch_id, allow_none=True
        )
        if general_result:
            analysis_list = general_result.get("analysis") or []
            for node, analysis in zip(batch.nodes, analysis_list):
                if not analysis:
                    continue

                summary = analysis.get("summary") or ""
                actor = analysis.get("actor") or ""
                if summary:
                    node_name = build_statement_name(node.node_type, node.start_line)
                    queries.append({
                        "query": (
                            f"MATCH (__cy_n__:{node.node_type} {{startLine: $start_line, directory: $directory, file_name: $file_name}})\n"
                            f"SET __cy_n__.endLine = $end_line, __cy_n__.name = $node_name, "
                            f"__cy_n__.summary = $summary, __cy_n__.node_code = $node_code, "
                            f"__cy_n__.token = $token, __cy_n__.procedure_name = $procedure_name, "
                            f"__cy_n__.has_children = $has_children\n"
                            f"RETURN __cy_n__"
                        ),
                        "params": {
                            "start_line": node.start_line,
                            "end_line": node.end_line,
                            "directory": self.full_directory,
                            "file_name": self.file_name,
                            "node_name": node_name,
                            "summary": str(summary),
                            "node_code": node.code,
                            "token": node.token,
                            "procedure_name": node.unit_name or "",
                            "has_children": node.has_children,
                        }
                    })

                    # Actor 노드 생성 + ROLE 관계
                    if actor:
                        queries.append({
                            "query": (
                                f"MATCH (__cy_n__:{node.node_type} {{startLine: $start_line, directory: $directory, file_name: $file_name}})\n"
                                f"MERGE (a:Actor {{name: $actor}})\n"
                                f"ON CREATE SET a.created_at = timestamp()\n"
                                f"MERGE (a)-[r:ROLE]->(__cy_n__)\n"
                                f"ON CREATE SET r.created_at = timestamp()\n"
                                f"RETURN a"
                            ),
                            "params": {
                                "start_line": node.start_line,
                                "directory": self.full_directory,
                                "file_name": self.file_name,
                                "actor": actor,
                            }
                        })

                    # unit_summary_store에는 리프 노드(자식이 없는 노드)만 저장
                    # → 부모 노드 summary는 자식 내용을 이미 포함하므로 중복 제거
                    # → procedure_summary 생성 시 스켈레톤+리프 방식으로 전달
                    if unit_summary_store is not None and node.unit_key and node.unit_key in unit_summary_store:
                        if not node.has_children:
                            key = f"{node.node_type}_{node.start_line}_{node.end_line}"
                            unit_summary_store[node.unit_key][key] = summary

                # --- CALL 관계 생성 ---
                for call_name in analysis.get('calls', []) or []:
                    if '.' in call_name:
                        package_raw, proc_raw = call_name.split('.', 1)
                        # 시스템 패키지 호출은 무시 (DBMS_OUTPUT, UTL_FILE 등)
                        if package_raw.strip().upper() in DBMS_SYSTEM_PACKAGES:
                            continue
                        queries.append({
                            "query": (
                                f"MATCH (__cy_c__:{node.node_type} {{startLine: $start_line, directory: $directory, file_name: $file_name}})\n"
                                f"MERGE (__cy_target__:PROCEDURE {{directory: $package_name, procedure_name: $proc_name}})\n"
                                f"MERGE (__cy_c__)-[__cy_r__:CALLS {{scope: 'external'}}]->(__cy_target__)\n"
                                f"RETURN __cy_c__, __cy_target__, __cy_r__"
                            ),
                            "params": {
                                "start_line": node.start_line,
                                "directory": self.full_directory,
                                "file_name": self.file_name,
                                "package_name": package_raw.strip(),
                                "proc_name": proc_raw.strip(),
                            }
                        })
                    else:
                        # 분석 범위 내 노드가 있으면 internal, 없으면 EXTERNAL placeholder 생성
                        queries.append({
                            "query": (
                                f"MATCH (__cy_c__:{node.node_type} {{startLine: $start_line, directory: $directory, file_name: $file_name}})\n"
                                f"OPTIONAL MATCH (__cy_existing__ {{procedure_name: $call_name}})\n"
                                f"WHERE (__cy_existing__:PROCEDURE OR __cy_existing__:FUNCTION) AND NOT __cy_existing__:EXTERNAL\n"
                                f"WITH __cy_c__, __cy_existing__\n"
                                f"FOREACH (_ IN CASE WHEN __cy_existing__ IS NOT NULL THEN [1] ELSE [] END |\n"
                                f"  MERGE (__cy_c__)-[:CALLS {{scope: 'internal'}}]->(__cy_existing__)\n"
                                f")\n"
                                f"FOREACH (_ IN CASE WHEN __cy_existing__ IS NULL THEN [1] ELSE [] END |\n"
                                f"  MERGE (__cy_ext__:PROCEDURE:EXTERNAL {{procedure_name: $call_name}})\n"
                                f"  MERGE (__cy_c__)-[:CALLS {{scope: 'external'}}]->(__cy_ext__)\n"
                                f")\n"
                                f"RETURN __cy_c__"
                            ),
                            "params": {
                                "start_line": node.start_line,
                                "directory": self.full_directory,
                                "file_name": self.file_name,
                                "call_name": call_name,
                            }
                        })

                # --- 변수 처리 ---
                var_queries = self._process_variable_usage(node, analysis.get('variables', []) or [])
                queries.extend(var_queries)

        # --- 테이블 처리 ---
        table_result = self.validate_dict_result(
            table_result, "table_result", batch.batch_id, allow_none=True
        )
        if table_result:
            table_queries = self._build_table_queries(batch, table_result)
            queries.extend(table_queries)

        return queries

    # =========================================================================
    # DBMS 전용 메서드
    # =========================================================================

    def _process_variable_usage(self, node: StatementNode, variables: List[Any]) -> List[Union[str, Dict[str, Any]]]:
        """변수 사용 마킹 및 역할 누적 처리"""
        queries: List[Union[str, Dict[str, Any]]] = []

        for var_info in variables:
            if isinstance(var_info, dict):
                var_name = var_info.get("name", "")
                var_role = var_info.get("role", "")
            else:
                var_name = str(var_info)
                var_role = ""

            if not var_name:
                continue

            usage_key = f"{node.start_line}_{node.end_line}"
            queries.append({
                "query": (
                    f"MATCH (__cy_v__:Variable {{name: $var_name, directory: $directory, file_name: $file_name}})\n"
                    f"SET __cy_v__.`{usage_key}` = 'Used'\n"
                    f"RETURN __cy_v__"
                ),
                "params": {
                    "var_name": var_name,
                    "directory": self.full_directory,
                    "file_name": self.file_name,
                }
            })
            # 자식 노드 → 변수 REFERENCES 관계 생성
            queries.append({
                "query": (
                    f"MATCH (__cy_n__:{node.node_type} {{startLine: $start_line, directory: $directory, file_name: $file_name}})\n"
                    f"MATCH (__cy_v__:Variable {{name: $var_name, directory: $directory, file_name: $file_name}})\n"
                    f"MERGE (__cy_n__)-[:REFERENCES]->(__cy_v__)\n"
                    f"RETURN __cy_n__, __cy_v__"
                ),
                "params": {
                    "start_line": node.start_line,
                    "var_name": var_name,
                    "directory": self.full_directory,
                    "file_name": self.file_name,
                }
            })

            if var_role and node.unit_key:
                if node.unit_key not in self._variable_roles:
                    self._variable_roles[node.unit_key] = {}
                if var_name not in self._variable_roles[node.unit_key]:
                    self._variable_roles[node.unit_key][var_name] = []
                if var_role not in self._variable_roles[node.unit_key][var_name]:
                    self._variable_roles[node.unit_key][var_name].append(var_role)

        return queries

    def _build_table_queries(
        self,
        batch: AnalysisBatch,
        table_result: Dict[str, Any]
    ) -> List[Union[str, Dict[str, Any]]]:
        """DML 테이블 분석 결과를 쿼리로 변환"""
        queries: List[Union[str, Dict[str, Any]]] = []
        node_map: Dict[Tuple[int, int], StatementNode] = {
            (node.start_line, node.end_line): node for node in batch.nodes
        }
        ranges = table_result.get('ranges', []) or []

        for range_entry in ranges:
            try:
                start_line = int(range_entry.get('startLine'))
                end_line = int(range_entry.get('endLine'))
            except (TypeError, ValueError) as e:
                raise RuntimeError(f"LLM 테이블 분석 결과에 잘못된 라인 번호: startLine={range_entry.get('startLine')}, endLine={range_entry.get('endLine')}") from e

            node = node_map.get((start_line, end_line))
            if not node:
                continue

            node_match_params = {
                "start_line": node.start_line,
                "directory": self.full_directory,
                "file_name": self.file_name,
            }

            if node.node_type == 'CREATE_TEMP_TABLE':
                queries.extend(self._process_create_temp_table(node, range_entry.get('tables', []), node_match_params))
                continue

            queries.extend(self._process_dml_tables(node, range_entry.get('tables', []), node_match_params))
            queries.extend(self._process_dblinks(node, range_entry.get('dbLinks', []), node_match_params))
            queries.extend(self._process_fk_relations(range_entry.get('fkRelations', [])))

        return queries

    def _process_create_temp_table(
        self,
        node: StatementNode,
        tables: List[Dict[str, Any]],
        node_match_params: Dict[str, Any]
    ) -> List[Union[str, Dict[str, Any]]]:
        """CREATE_TEMP_TABLE 처리"""
        queries: List[Union[str, Dict[str, Any]]] = []

        for entry in tables:
            table_name = (entry.get('table') or '').strip()
            if not table_name:
                continue

            schema_part_raw, name_part_raw, _ = parse_table_identifier(table_name)
            schema_part = self._apply_name_case(schema_part_raw or self.default_schema)
            name_part = self._apply_name_case(name_part_raw)

            queries.append({
                "query": (
                    f"MATCH (__cy_n__:{node.node_type} {{startLine: $start_line, directory: $directory, file_name: $file_name}})\n"
                    f"SET __cy_n__:Table, __cy_n__.name = $name, __cy_n__.schema = $schema, __cy_n__.db = $db\n"
                    f"RETURN __cy_n__"
                ),
                "params": {
                    **node_match_params,
                    "name": name_part,
                    "schema": schema_part,
                    "db": self.dbms,
                }
            })

        return queries

    def _process_dml_tables(
        self,
        node: StatementNode,
        tables: List[Dict[str, Any]],
        node_match_params: Dict[str, Any]
    ) -> List[Union[str, Dict[str, Any]]]:
        """일반 DML 테이블 처리"""
        queries: List[Union[str, Dict[str, Any]]] = []
        table_merge_cache: Dict[Tuple[str, str, bool], Dict[str, Any]] = {}

        for entry in tables:
            table_name = (entry.get('table') or '').strip()
            if not table_name:
                continue

            # --- 테이블 정규화 ---
            schema_part_raw, name_part_raw, db_link_value = parse_table_identifier(table_name)
            schema_part, name_part, skip_case_conversion = self._resolve_table_name_with_ddl(schema_part_raw, name_part_raw)

            cache_key = (name_part, schema_part, skip_case_conversion)
            if cache_key not in table_merge_cache:
                table_merge_cache[cache_key] = self._build_table_merge(
                    name_part, schema_part, preserve_vars=['__cy_n__'], skip_case_conversion=skip_case_conversion
                )
            table_merge_result = table_merge_cache[cache_key]

            # --- 관계 생성 ---
            access_mode = (entry.get('accessMode') or entry.get('mode') or 'r').lower()
            rel_types = [TABLE_RELATIONSHIP_MAP.get(mode, mode.upper()) for mode in ['r', 'w'] if mode in access_mode]

            table_desc_raw = entry.get('tableDescription') or entry.get('description') or ''
            bucket_key = self._record_table_summary(schema_part, name_part, table_desc_raw, skip_case_conversion=skip_case_conversion)

            rel_merge_parts = [f"MERGE (__cy_n__)-[__cy_r{i}__:{rel_type}]->(__cy_t__)" for i, rel_type in enumerate(rel_types)]
            db_link_set = f"\nSET __cy_t__.db_link = COALESCE(__cy_t__.db_link, $db_link)" if db_link_value else ""
            rel_merge_str = "\n".join(rel_merge_parts)

            table_query = (
                f"MATCH (__cy_n__:{node.node_type} {{startLine: $start_line, directory: $directory, file_name: $file_name}})\n"
                f"WITH __cy_n__\n"
                f"{table_merge_result['query']}\n"
                f"SET __cy_t__.db = coalesce(__cy_t__.db, $db)"
                f"{db_link_set}"
                f"\n{rel_merge_str}" if rel_merge_str else ""
                f"\nRETURN __cy_n__, __cy_t__"
            )

            params = {**node_match_params, **table_merge_result["params"]}
            if db_link_value:
                params["db_link"] = db_link_value

            queries.append({"query": table_query, "params": params})

            # --- 컬럼 처리 ---
            column_queries = self._process_table_columns(
                entry.get('columns', []),
                name_part,
                schema_part,
                bucket_key,
                skip_case_conversion
            )
            queries.extend(column_queries)

        return queries

    def _process_table_columns(
        self,
        columns: List[Dict[str, Any]],
        table_name: str,
        schema: str,
        bucket_key: Tuple[str, str],
        skip_case_conversion: bool
    ) -> List[Union[str, Dict[str, Any]]]:
        """컬럼 처리"""
        queries: List[Union[str, Dict[str, Any]]] = []

        table_merge_result = self._build_table_merge(table_name, schema, preserve_vars=None, skip_case_conversion=skip_case_conversion)
        ddl_meta, _ = self._get_ddl_metadata(schema, table_name)
        ddl_columns = ddl_meta.get('columns', {}) if ddl_meta else {}

        for column in columns:
            column_name_raw = (column.get('name') or '').strip()
            if not column_name_raw:
                continue

            column_name = self._resolve_column_name_with_ddl(column_name_raw, ddl_columns)

            raw_dtype = column.get('dtype') or ''
            raw_column_desc = (column.get('description') or column.get('comment') or '').strip()

            self._record_column_summary(
                bucket_key,
                column_name,
                raw_column_desc,
                dtype=raw_dtype,
                nullable=column.get('nullable', True),
                examples=column.get('examples') or [],
            )

            converted_name_part = table_merge_result["converted_table_name"]
            converted_schema_part = table_merge_result["effective_schema"]
            fqn = '.'.join(filter(None, [converted_schema_part, converted_name_part, column_name])).lower()

            col_query = (
                f"{table_merge_result['query']}\nWITH __cy_t__\n"
                f"MERGE (__cy_c__:Column {{fqn: $fqn}})\n"
                f"SET __cy_c__.name = $col_name, __cy_c__.dtype = $col_type, "
                f"__cy_c__.description = $col_desc, __cy_c__.description_source = 'code', __cy_c__.nullable = $nullable\n"
                f"MERGE (__cy_t__)-[__cy_r__:HAS_COLUMN]->(__cy_c__)\n"
                f"RETURN __cy_t__, __cy_c__, __cy_r__"
            )

            queries.append({
                "query": col_query,
                "params": {
                    **table_merge_result["params"],
                    "fqn": fqn,
                    "col_name": column_name,
                    "col_type": raw_dtype,
                    "col_desc": raw_column_desc,
                    "nullable": column.get('nullable', True),
                }
            })

        return queries

    def _process_dblinks(
        self,
        node: StatementNode,
        db_links: List[Dict[str, Any]],
        node_match_params: Dict[str, Any]
    ) -> List[Union[str, Dict[str, Any]]]:
        """DBLink 처리"""
        queries: List[Union[str, Dict[str, Any]]] = []

        for link_item in db_links:
            link_name_raw = (link_item.get('name') or '').strip()
            if not link_name_raw:
                continue

            mode = (link_item.get('mode') or 'r').lower()
            schema_link_raw, name_link_raw, link_name = parse_table_identifier(link_name_raw)
            schema_link = self._apply_name_case(schema_link_raw or self.default_schema)
            name_link = self._apply_name_case(name_link_raw)
            remote_merge_result = self._build_table_merge(name_link, schema_link)

            dblink_query = (
                f"{remote_merge_result['query']}\n"
                f"SET __cy_t__.db_link = $link_name\n"
                f"WITH __cy_t__\n"
                f"MERGE (__cy_l__:DBLink {{name: $link_name}})\n"
                f"MERGE (__cy_l__)-[__cy_r1__:LINKS_TO]->(__cy_t__)\n"
                f"WITH __cy_t__, __cy_l__\n"
                f"MATCH (__cy_n__:{node.node_type} {{startLine: $start_line, directory: $directory, file_name: $file_name}})\n"
                f"MERGE (__cy_n__)-[__cy_r2__:DB_LINK {{mode: $mode}}]->(__cy_t__)\n"
                f"RETURN __cy_l__, __cy_t__, __cy_n__"
            )

            queries.append({
                "query": dblink_query,
                "params": {
                    **remote_merge_result["params"],
                    **node_match_params,
                    "link_name": link_name,
                    "mode": mode,
                }
            })

        return queries

    def _process_fk_relations(
        self,
        fk_relations: List[Dict[str, Any]]
    ) -> List[Union[str, Dict[str, Any]]]:
        """FK 관계 처리 (FK_TO_TABLE과 FK_TO를 한 번의 루프에서 생성)"""
        queries: List[Union[str, Dict[str, Any]]] = []

        for relation in fk_relations:
            src_table = (relation.get('sourceTable') or '').strip()
            tgt_table = (relation.get('targetTable') or '').strip()
            src_columns = [c.strip() for c in (relation.get('sourceColumns') or []) if c]
            tgt_columns = [c.strip() for c in (relation.get('targetColumns') or []) if c]

            if not (src_table and tgt_table and src_columns and tgt_columns):
                continue

            src_schema_raw, src_name_raw, _ = parse_table_identifier(src_table)
            tgt_schema_raw, tgt_name_raw, _ = parse_table_identifier(tgt_table)

            src_schema, src_name, _ = self._resolve_table_name_with_ddl(src_schema_raw, src_name_raw)
            tgt_schema, tgt_name, _ = self._resolve_table_name_with_ddl(tgt_schema_raw, tgt_name_raw)

            effective_src_schema = src_schema or self._apply_name_case(self.default_schema)
            effective_tgt_schema = tgt_schema or self._apply_name_case(self.default_schema)

            for src_col, tgt_col in zip(src_columns, tgt_columns):
                converted_src_col = self._apply_name_case(src_col)
                converted_tgt_col = self._apply_name_case(tgt_col)

                queries.append({
                    "query": (
                        f"MATCH (__cy_st__:Table {{schema: $src_schema, name: $src_name, db: $db}})\n"
                        f"MATCH (__cy_tt__:Table {{schema: $tgt_schema, name: $tgt_name, db: $db}})\n"
                        f"MERGE (__cy_st__)-[__cy_r__:FK_TO_TABLE {{sourceColumn: $src_col, targetColumn: $tgt_col}}]->(__cy_tt__)\n"
                        f"ON CREATE SET __cy_r__.type = 'many_to_one', __cy_r__.source = 'procedure'\n"
                        f"RETURN __cy_st__, __cy_tt__, __cy_r__"
                    ),
                    "params": {
                        "src_schema": effective_src_schema,
                        "src_name": src_name,
                        "tgt_schema": effective_tgt_schema,
                        "tgt_name": tgt_name,
                        "db": self.dbms,
                        "src_col": converted_src_col,
                        "tgt_col": converted_tgt_col,
                    }
                })

                src_fqn = '.'.join(filter(None, [effective_src_schema, src_name, converted_src_col])).lower()
                tgt_fqn = '.'.join(filter(None, [effective_tgt_schema, tgt_name, converted_tgt_col])).lower()

                queries.append({
                    "query": (
                        f"MATCH (__cy_sc__:Column {{fqn: $src_fqn}})\n"
                        f"MATCH (__cy_dc__:Column {{fqn: $tgt_fqn}})\n"
                        f"MERGE (__cy_sc__)-[__cy_r__:FK_TO_COLUMN]->(__cy_dc__)\n"
                        f"ON CREATE SET __cy_r__.source = 'procedure'\n"
                        f"RETURN __cy_sc__, __cy_dc__, __cy_r__"
                    ),
                    "params": {
                        "src_fqn": src_fqn,
                        "tgt_fqn": tgt_fqn,
                    }
                })

        return queries

    def _build_table_merge(self, table_name: str, schema: Optional[str], preserve_vars: Optional[List[str]] = None, skip_case_conversion: bool = False) -> Dict[str, Any]:
        """테이블 MERGE 쿼리 생성 (Schema 노드 및 BELONGS_TO 관계 포함)"""
        effective_schema = self._get_effective_schema(schema)

        if not skip_case_conversion:
            effective_schema = self._apply_name_case(effective_schema)
            converted_table_name = self._apply_name_case(table_name)
        else:
            converted_table_name = table_name

        if preserve_vars:
            with_vars = ", ".join(preserve_vars + ["__cy_s__"])
        else:
            with_vars = "__cy_s__"

        return {
            "query": (
                f"MERGE (__cy_s__:Schema {{db: $db, name: $schema_name}})\n"
            f"WITH {with_vars}\n"
                f"MERGE (__cy_t__:Table {{name: $table_name, schema: $schema_name, db: $db}})\n"
            f"MERGE (__cy_t__)-[:BELONGS_TO]->(__cy_s__)"
            ),
            "params": {
                "db": self.dbms,
                "schema_name": effective_schema,
                "table_name": converted_table_name,
            },
            "effective_schema": effective_schema,
            "converted_table_name": converted_table_name,
        }

    def _record_table_summary(self, schema: Optional[str], name: str, description: Optional[str], skip_case_conversion: bool = False) -> Tuple[str, str]:
        """테이블 설명 누적"""
        effective_schema = self._get_effective_schema(schema)

        if skip_case_conversion:
            schema_key = effective_schema
            name_key = name
        else:
            schema_key = self._apply_name_case(effective_schema)
            name_key = self._apply_name_case(name)

        bucket = self._table_summary_store.setdefault((schema_key, name_key), {"summaries": set(), "columns": {}})
        text = (description or '').strip()
        if text:
            bucket["summaries"].add(text)
        return (schema_key, name_key)

    def _record_column_summary(
        self,
        table_key: Tuple[str, str],
        column_name: str,
        description: Optional[str],
        dtype: Optional[str] = None,
        nullable: Optional[bool] = None,
        examples: Optional[List[str]] = None,
    ):
        """컬럼 설명 누적"""
        text = (description or '').strip()
        bucket = self._table_summary_store.setdefault(table_key, {"summaries": set(), "columns": {}})
        columns = bucket["columns"]
        canonical = self._apply_name_case(column_name)
        entry = columns.get(canonical)
        if entry is None:
            entry = {"name": canonical, "summaries": set(), "dtype": (dtype or ''), "nullable": True if nullable is None else bool(nullable), "examples": set()}
            columns[canonical] = entry
        if dtype is not None and not entry.get("dtype"):
            entry["dtype"] = dtype
        if nullable is not None:
            entry["nullable"] = bool(nullable)
        if text:
            entry["summaries"].add(text)
        if examples:
            for v in examples:
                if v is not None:
                    s = str(v).strip()
                    if s:
                        entry["examples"].add(s)
