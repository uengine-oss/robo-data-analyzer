"""공통 AST 프로세서 베이스 클래스

템플릿 메서드 패턴으로 공통 파이프라인을 정의합니다.

파이프라인:
- Phase 1: build_static_graph_queries() - 정적 그래프 쿼리 생성
- Phase 1.5: _generate_node_contexts() - 노드 컨텍스트 생성 (부모 + 이전 형제)
- Phase 2: run_llm_analysis() - LLM 분석

전략별 구현 필요:
- _collect_nodes(): AST 수집
- _invoke_llm(): LLM 호출
- _build_analysis_queries(): 분석 결과 쿼리 변환
- _process_unit_summaries(): 단위 요약 처리
"""

from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, Tuple, Set, Union

from config.settings import settings
from util.text_utils import calculate_code_token, log_process
from analyzer.pipeline_control import pipeline_controller

from analyzer.strategy.base.statement_node import StatementNode
from analyzer.strategy.base.batch import AnalysisBatch, BatchPlanner

# 설정에서 가져오는 상수
MAX_CONCURRENCY = settings.concurrency.max_concurrency
MAX_CONTEXT_TOKEN = settings.batch.max_context_token


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
        log_process("ANALYZE", "PHASE1", f"🏗️ {self.full_directory} 정적 그래프 생성")
        
        # AST 수집
        self._nodes, self._unit_info = self._collect_nodes()
        
        if not self._nodes:
            log_process("ANALYZE", "PHASE1", f"⚠️ {self.full_directory}: 분석 대상 노드 없음")
            return []
        
        # 정적 노드 쿼리 생성 (파라미터화된 Dict 쿼리도 포함)
        queries: List[Union[str, Dict[str, Any]]] = []
        for node in self._nodes:
            queries.extend(self._build_static_node_queries(node))
        
        # 관계 쿼리 생성
        queries.extend(self._build_relationship_queries())
        
        log_process("ANALYZE", "PHASE1", f"✅ {self.full_directory}: {len(queries)}개 쿼리 생성")
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
            log_process("ANALYZE", "CONTEXT", "⏭️ 컨텍스트 생성이 필요한 부모 노드 없음")
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
        
        log_process("ANALYZE", "CONTEXT", f"🔄 노드 컨텍스트 생성 시작: {len(parent_nodes)}개 노드")
        
        # 순차적으로 처리 (깊이 순서 보장)
        # 같은 깊이의 노드들은 병렬 처리 가능
        current_depth = -1
        current_batch: List[StatementNode] = []
        
        async def process_context_batch(batch: List[StatementNode]) -> None:
            """같은 깊이의 노드들을 병렬로 처리"""
            semaphore = asyncio.Semaphore(min(self.max_workers, len(batch)))
            
            async def process_one(node: StatementNode) -> None:
                async with semaphore:
                    try:
                        # 부모의 context_ready_event 대기 (있으면)
                        if node.parent and node.parent.needs_context_generation(excluded_types):
                            await node.parent.context_ready_event.wait()
                        
                        # 스켈레톤 코드 생성 (부모 틀)
                        skeleton = node.get_skeleton_code()
                        
                        # 조상 컨텍스트 수집
                        ancestor_ctx = node.get_ancestor_context(MAX_CONTEXT_TOKEN)
                        
                        # 이전 형제 원문 코드
                        sibling_code = node.get_sibling_code()
                        
                        # LLM 호출하여 컨텍스트 추출 (부모 + 형제)
                        parent_ctx, sibling_ctx = await self._extract_node_context(
                            skeleton, ancestor_ctx, sibling_code
                        )
                        
                        node.context = parent_ctx
                        node.sibling_context = sibling_ctx
                        log_process("ANALYZE", "CONTEXT", f"✅ 컨텍스트 생성 완료: {node.node_type}[{node.start_line}~{node.end_line}]")
                    except Exception as e:
                        log_process("ANALYZE", "CONTEXT", f"❌ 컨텍스트 생성 실패 (치명적): {node.node_type}[{node.start_line}]: {e}", logging.ERROR)
                        # 컨텍스트 없이 분석하면 별칭 해석 오류 등으로 결과가 엉망이 됨
                        # 예외를 다시 발생시켜서 실패를 명확히 표시
                        raise
                    finally:
                        node.context_ready_event.set()
            
            await asyncio.gather(*[process_one(n) for n in batch])
        
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
        
        log_process("ANALYZE", "CONTEXT", f"✅ 노드 컨텍스트 생성 완료: {len(parent_nodes)}개")

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
        
        async def process_batch(batch: AnalysisBatch, semaphore: asyncio.Semaphore) -> Tuple[List[str], Dict[str, Any]]:
            """배치 처리 후 쿼리와 분석 결과 반환. 노드에 summary도 설정.
            
            핵심: 부모 노드는 자식 completion_event를 기다린 후 실행됨
            → 깊이 계산 없이 자연스럽게 leaf → parent 순서 보장
            
            중요: 
            - try/finally로 completion_event.set()을 보장하여 데드락 방지
            - 자식 중 ok=False가 있으면 부모도 ok=False (불완전 요약 전파)
            """
            async with semaphore:
                # 배치 시작 전 일시정지/중단 체크
                if not await pipeline_controller.check_continue():
                    raise RuntimeError("파이프라인 중단됨")
                
                try:
                    # 1. 배치 내 모든 노드의 자식 완료 및 부모 컨텍스트 준비를 기다림
                    for node in batch.nodes:
                        # 부모 컨텍스트가 준비될 때까지 대기
                        if node.parent:
                            await node.parent.context_ready_event.wait()
                        
                        if node.has_children:
                            for child in node.children:
                                await child.completion_event.wait()
                                # 자식 중 하나라도 실패하면 부모도 불완전
                                if not child.ok:
                                    node.ok = False
                    
                    log_process("ANALYZE", "LLM", f"배치 #{batch.batch_id} 처리 중 ({len(batch.nodes)}개 노드)")
                    llm_result = await self._invoke_llm(batch)
                    
                    # 2. 노드에 summary 설정 (전략별 결과 형식에 따라)
                    self._apply_summary_to_nodes(batch, llm_result)
                    
                    queries = self._build_analysis_queries(batch, llm_result, unit_summary_store)
                    # 배치 완료 즉시 Neo4j 저장 + 스트리밍
                    if on_queries_ready and queries:
                        await on_queries_ready(queries, batch.batch_id, len(batches))
                    return queries, {"batch": batch, "result": llm_result}
                except Exception:
                    # 배치 실패 시 모든 노드를 ok=False로 마킹
                    for node in batch.nodes:
                        node.ok = False
                    raise
                finally:
                    # 3. 무조건 completion_event 설정 (실패해도 부모가 대기하지 않도록)
                    for node in batch.nodes:
                        node.completion_event.set()
        
        def collect_results(batch_results: list, batches_list: List[AnalysisBatch], level_name: str) -> Tuple[int, List[Dict[str, Any]]]:
            """배치 결과를 수집하고 (실패 수, 실패 상세 정보) 반환."""
            nonlocal all_queries
            fail_count = 0
            failed_details: List[Dict[str, Any]] = []
            
            for i, result in enumerate(batch_results):
                if isinstance(result, Exception):
                    batch = batches_list[i] if i < len(batches_list) else None
                    batch_id = batch.batch_id if batch else i
                    node_ranges = ", ".join(f"L{n.start_line}-{n.end_line}" for n in batch.nodes) if batch else "unknown"
                    error_msg = str(result)[:100]  # 최대 100자
                    
                    log_process("ANALYZE", "ERROR", f"[{level_name}] 배치 #{batch_id} 실패 ({node_ranges}): {error_msg}", logging.ERROR)
                    fail_count += 1
                    failed_details.append({
                        "batch_id": batch_id,
                        "node_ranges": node_ranges,
                        "error": error_msg
                    })
                else:
                    queries, _ = result
                    all_queries.extend(queries)
            return fail_count, failed_details
        
        # 모든 배치 병렬 실행 (completion_event가 순서 보장)
        semaphore = asyncio.Semaphore(min(self.max_workers, len(batches)))
        batch_results = await asyncio.gather(
            *[process_batch(b, semaphore) for b in batches],
            return_exceptions=True
        )
        fail_count, failed_details = collect_results(batch_results, batches, "LLM")
        failed_batch_count += fail_count
        all_failed_details.extend(failed_details)
        
        # 배치 실패 시 즉시 중단 - 부분 실패 허용 안함
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
            raw_refs = analysis.get("externalRefs")
            if isinstance(raw_refs, list):
                node.external_refs = [
                    {"kind": str(r.get("kind", "")), "target": str(r.get("target", ""))}
                    for r in raw_refs if isinstance(r, dict) and r.get("kind") and r.get("target")
                ]
            else:
                node.external_refs = []

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
    
    def _split_summaries_by_token(self, summaries: dict, max_token: int) -> List[dict]:
        """토큰 기준으로 summaries를 청크로 분할합니다."""
        if not summaries:
            return []
        
        chunks = []
        current_chunk = {}
        current_tokens = 0
        
        for key, value in summaries.items():
            item_text = f"{key}: {value}"
            item_tokens = calculate_code_token(item_text)
            
            if current_tokens + item_tokens > max_token and current_chunk:
                chunks.append(current_chunk)
                current_chunk = {}
                current_tokens = 0
            
            current_chunk[key] = value
            current_tokens += item_tokens
        
        if current_chunk:
            chunks.append(current_chunk)
        
        return chunks

    # =========================================================================
    # 공통 관계 쿼리 빌더 (파라미터화)
    # =========================================================================

    def _build_contains_query(self, parent: "StatementNode", child: "StatementNode") -> Dict[str, Any]:
        """CONTAINS 관계 쿼리 (파라미터화)"""
        return {
            "query": (
                f"MATCH (__cy_p__:{parent.node_type} {{startLine: $parent_line, directory: $directory, file_name: $file_name}})\n"
                f"MATCH (__cy_c__:{child.node_type} {{startLine: $child_line, directory: $directory, file_name: $file_name}})\n"
            f"MERGE (__cy_p__)-[__cy_r__:CONTAINS]->(__cy_c__)\n"
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

