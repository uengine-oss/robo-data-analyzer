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
import re
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, Tuple, Set, Union

from util.logger import log_process
from util.code_text_utils import parse_table_identifier
from analyzer.common.cypher.schema_resolver import resolve_schema
from analyzer.common.pipeline.pipeline_control import pipeline_controller

from analyzer.strategy.dbms.analysis.ast_collector import StatementCollector
from analyzer.strategy.dbms.linking import ddl_table_matcher
from analyzer.strategy.dbms.common.domain.analysis_constants import (
    MAX_CONCURRENCY,
    MAX_CONTEXT_TOKEN,
    PROCEDURE_TYPES,
    TABLE_RELATIONSHIP_MAP,
)
from analyzer.strategy.dbms.common.cypher.graph_queries import (
    build_column_queries,
    build_create_temp_table_queries,
    build_dblink_queries,
    build_dml_table_relation_queries,
    build_fk_to_column_queries,
    build_fk_to_table_queries,
    build_has_procedure_queries,
    build_next_queries,
    build_package_queries,
    build_parent_of_queries,
    build_static_node_queries,
    build_summary_update_queries,
)
from analyzer.strategy.dbms.common.domain.naming_helpers import build_statement_name
from analyzer.strategy.dbms.common.models.node_models import (
    AnalysisBatch,
    BatchPlanner,
    ProcedureInfo,
    StatementNode,
)
from util.llm_rule_runner import prompts
from util.llm_call_safety import AsyncBatchExecutor, LLMRateLimiter


def _extract_enclosing_parameters(batch_nodes: List[StatementNode]) -> str:
    """batch 의 enclosing PROCEDURE/FUNCTION 의 parameters 텍스트.

    같은 unit 의 batch 라면 첫 노드의 enclosing 만 확인하면 충분.
    parameters 가 비어있으면 빈 문자열 반환.
    """
    for node in batch_nodes:
        cur: Optional[StatementNode] = node
        while cur is not None:
            if cur.node_type in PROCEDURE_TYPES and cur.parameters:
                return cur.parameters
            cur = cur.parent
    return ""


def _find_enclosing_proc(node: StatementNode) -> Optional[StatementNode]:
    cur: Optional[StatementNode] = node
    while cur is not None:
        if cur.node_type in PROCEDURE_TYPES:
            return cur
        cur = cur.parent
    return None


def _extract_referenced_variables(batch_nodes: List[StatementNode]) -> str:
    """batch 코드에서 참조되는 enclosing 프로시저 변수 + 사용 라인 매핑.

    형식: ``<name> (<type>) := <init_value> → 사용 라인: 12, 15-17``
    init_value 가 없으면 ``:= <init_value>`` 부분 생략.
    사용 라인 매핑은 batch 안 노드들의 code (``"<line>: <text>"`` 형식) 에서 수집.
    참조 변수가 없으면 빈 문자열.
    """
    if not batch_nodes:
        return ""
    proc = _find_enclosing_proc(batch_nodes[0])
    if proc is None:
        return ""

    proc_vars: List[StatementNode] = []
    def _collect(n: StatementNode) -> None:
        if n.node_type == "VARIABLE" and n.name:
            proc_vars.append(n)
        for c in n.children:
            _collect(c)
    _collect(proc)
    if not proc_vars:
        return ""

    # batch 코드 라인별로 분리해 변수명 매칭 → 사용 라인 수집
    line_re = re.compile(r"^(\d+):\s?(.*)$")
    code_lines_by_no: List[Tuple[int, str]] = []
    for n in batch_nodes:
        for raw_line in (n.code or "").split("\n"):
            m = line_re.match(raw_line)
            if m:
                code_lines_by_no.append((int(m.group(1)), m.group(2)))
    if not code_lines_by_no:
        return ""

    output: List[str] = []
    seen: Set[str] = set()
    for v in proc_vars:
        if v.name in seen:
            continue
        var_pattern = re.compile(rf"\b{re.escape(v.name)}\b")
        usage_lines: Set[int] = set()
        for line_no, text in code_lines_by_no:
            if var_pattern.search(text):
                usage_lines.add(line_no)
        if not usage_lines:
            continue
        seen.add(v.name)
        type_part = f" ({v.variable_type})" if v.variable_type else ""
        init_part = f" := {v.init_value}" if v.init_value else ""
        line_str = ddl_table_matcher.format_line_ranges(sorted(usage_lines))
        output.append(f"{v.name}{type_part}{init_part} → 사용 라인: {line_str}")
    return "\n".join(output)


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
    - _static_node_item(): 정적 노드 1건의 UNWIND 입력 dict
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

        source_content: 원본 소스 파일 내용. AST JSON 에 code 속성이 없을 때
        startLine/endLine 으로 코드를 슬라이스해 보강하는 데이터 출처.
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

        # 파일별 독립 Executor (파일끼리 완전 병렬, 파일 안은 continuous)
        self._batch_executor = AsyncBatchExecutor(
            LLMRateLimiter(max_concurrent=MAX_CONCURRENCY, spacing_ms=0)
        )

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
    def _static_node_item(self, node: StatementNode) -> Dict[str, Any]:
        """정적 노드 1건의 UNWIND 입력 dict.

        ``build_static_node_queries`` 가 label 별 그룹화 → label 별 1 UNWIND 쿼리 생성.
        """
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
        ancestor_tree: str,
        sibling_brief: str,
        node_label: str,
    ) -> Tuple[str, str]:
        """자기 한 줄 디렉토리 항목 + 직전 형제 한 줄 추출.

        v5.0 — 디렉토리 트리 형식. 자기 출력은 ``<TYPE>[s-e]: <한 줄>`` 강제.
        자식이 받아 ancestor_tree 위에 들여쓰기로 쌓아 트리 자동 완성.

        Args:
            skeleton_code: 자기 코드 스켈레톤 (자식 구간 .... 압축)
            ancestor_tree: 조상 트리 (디렉토리 형식, ★ 현재 위치 마커 포함)
            sibling_brief: 직전 형제 한 줄 (``<TYPE>[s-e]: ...``) 또는 ""
            node_label: 자기 식별자 (``<TYPE>[s-e]``)

        Returns:
            (parent_context, sibling_context) — 둘 다 한 줄 (또는 sibling_context 는 "")
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

        queries: List[Union[str, Dict[str, Any]]] = []

        # 1) 정적 노드 (FILE/TRIGGER/PROCEDURE/...) — UNWIND, label 별 그룹화.
        #    FILE 노드가 PACKAGE 관계 전에 MERGE 되어야 BELONGS_TO_PACKAGE 가능.
        static_items = [self._static_node_item(node) for node in self._nodes]
        queries.extend(build_static_node_queries(static_items))

        # 2) PACKAGE 노드 + FILE→PACKAGE BELONGS_TO_PACKAGE 관계
        #    (반드시 FILE 노드 MERGE 후 호출 — graph_queries.build_package_queries 의
        #     두 번째 쿼리 MATCH (:FILE) 가 실제로 노드를 찾을 수 있어야 함)
        queries.extend(self._build_package_queries())

        # 3) AST 트리 관계 (PARENT_OF/NEXT/HAS_PROCEDURE)
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
                # v5.0 — 디렉토리 트리 형식 (★ 현재 위치 마커 포함)
                ancestor_tree = node.get_ancestor_tree(max_tokens=MAX_CONTEXT_TOKEN, marker="★ 현재")
                sibling_brief = node.get_previous_sibling_brief()
                node_label = node.get_node_label()
                parent_ctx, sibling_ctx = await self._extract_node_context(
                    skeleton, ancestor_tree, sibling_brief, node_label,
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
            """같은 깊이의 노드를 파일 전용 Executor 로 continuous 병렬 처리.

            깊이 간 순서는 외부 루프가 순차로 호출 (부모 깊이 context_ready_event
            가 모두 set 된 후에야 자식 깊이가 시작됨).
            """
            await self._batch_executor.run_batch(
                items=batch,
                processor=process_one,
                label=f"DBMS_NODE_CONTEXT:{self.full_directory}",
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

                # NB: 구조 래퍼(TRIGGER/PROCEDURE/FUNCTION/BEGIN/FILE) 는 NON_ANALYSIS_TYPES 라
                # BatchPlanner 가 스킵 → 이 분기 결코 실행 안 됨.
                # 구조 래퍼의 analysis_map 채움은 모든 배치 종료 후
                # _propagate_analysis_map_to_structural_wrappers() 에서 일괄 처리.

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

        # 모든 배치를 파일 전용 Executor 로 continuous 병렬 실행
        # (completion_event 로 부모-자식 순서 보장, 파일별 독립 Semaphore 로 병렬)
        batch_results = await self._batch_executor.run_batch(
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

        # 배치 후 propagation: 구조 래퍼(TRIGGER/PROCEDURE/FUNCTION/BEGIN/FILE)의 analysis_map 채움.
        # NON_ANALYSIS 라 BatchPlanner 가 스킵한 노드들은 스스로 LLM 분석을 받지 않으므로,
        # 분석된 자식들의 analysis_map 을 합집합으로 누적해 proc_root 가 phase_summary 에서 비어있지 않도록 보장.
        self._propagate_analysis_map_to_structural_wrappers()

        log_process("ANALYZE", "PHASE2", f"✅ {self.full_directory}: {len(all_queries)}개 업데이트 쿼리")
        return all_queries, failed_batch_count, all_failed_details, unit_summary_store

    def _propagate_analysis_map_to_structural_wrappers(self) -> None:
        """LLM 분석에서 제외된 구조 래퍼 노드의 analysis_map 을 자식 누적값으로 채운다.

        흐름:
        1. 분석된 노드(node_analysis 거친 analyzable=True 노드) 는 LLM 결과로 analysis_map 가 이미 설정됨
        2. 구조 래퍼(NON_ANALYSIS_TYPES — TRIGGER/PROCEDURE/FUNCTION/BEGIN/FILE 등) 는 batches 에서 스킵돼 analysis_map = {}
        3. 이 메서드가 모든 batches 완료 후 트리를 bottom-up 순회해 빈 analysis_map 에 자식 합집합을 채움

        결과: proc_root.analysis_map 은 항상 그 프로시저 하위에서 누적된 모든 rules/questions 를 보유.
        """
        def walk(node: Any) -> None:
            for child in getattr(node, "children", None) or []:
                walk(child)
            existing = getattr(node, "analysis_map", None) or {}
            has_own_data = bool(existing.get("rules")) or bool(existing.get("questions"))
            if has_own_data:
                return  # LLM 이 직접 채운 노드 — 그대로 유지
            # 구조 래퍼 (또는 분석 결과가 비어 있던 노드) → 자식 누적
            merged_rules: List[Any] = []
            merged_questions: List[Any] = []
            for child in getattr(node, "children", None) or []:
                cmap = getattr(child, "analysis_map", None) or {}
                merged_rules.extend(cmap.get("rules") or [])
                merged_questions.extend(cmap.get("questions") or [])
            if merged_rules or merged_questions:
                node.analysis_map = {"rules": merged_rules, "questions": merged_questions}

        for root in (n for n in (self._nodes or []) if getattr(n, "parent", None) is None):
            walk(root)

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
            node.actor = analysis.get("actor") or ""  # phase_summary 가 unit-root 에 부착
            # Bottom-up 누적 — rules + questions 를 map 으로 보관
            # 자식 batch 완료 전에 반드시 저장되어야 부모 batch 가 참조 가능
            node.analysis_map = {
                "rules": list(analysis.get("rules") or []),
                "questions": list(analysis.get("questions") or []),
            }

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
        """Package 노드 생성 + FILE -[BELONGS_TO_PACKAGE]-> Package 관계 (graph_queries 위임)."""
        return build_package_queries(
            directory=self.directory,
            full_directory=self.full_directory,
            file_name=self.file_name,
        )

    # =========================================================================
    # 공통 관계 빌더 — UNWIND row item 으로 누적용 (호출자가 list 모음 후 build_X_queries 1회)
    # =========================================================================

    def _has_procedure_item(self, parent: "StatementNode", child: "StatementNode") -> Dict[str, Any]:
        """HAS_PROCEDURE 관계용 row item (FILE → PROCEDURE/FUNCTION).

        MATCH 키 = `_node_match_name(node)` — MERGE 키와 동일 정책 (DbmsAstProcessor 가 override).
        """
        return {
            "parent_type": parent.node_type,
            "parent_name": self._node_match_name(parent),
            "child_type":  child.node_type,
            "child_name":  self._node_match_name(child),
            "directory":   self.full_directory,
            "file_name":   self.file_name,
        }

    def _parent_of_item(self, parent: "StatementNode", child: "StatementNode") -> Dict[str, Any]:
        """PARENT_OF 관계용 row item."""
        return {
            "parent_type": parent.node_type,
            "parent_name": self._node_match_name(parent),
            "child_type":  child.node_type,
            "child_name":  self._node_match_name(child),
            "directory":   self.full_directory,
            "file_name":   self.file_name,
        }

    def _node_match_name(self, node: "StatementNode") -> str:
        """기본 구현 — DbmsAstProcessor 에서 override (DBMS 의 라벨별 name 정책).

        BaseAstProcessor 단위에서는 단순 `node.name` 또는 fallback. Framework strategy
        는 자체 ast processor 가 별도 구현.
        """
        return node.name or f"{node.node_type}[{node.start_line}]"


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
        own_schema: str,
        name_case: str,
        name_to_schema: Dict[str, str],
        ddl_table_metadata: Optional[Dict[Tuple[str, str], Dict[str, Any]]] = None,
        ddl_table_index: Optional[Dict[str, Dict[str, Any]]] = None,
        ddl_pattern: Optional[Any] = None,  # re.Pattern
        source_content: str = "",
        datasource_name: str = "",
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

        if not dbms:
            raise ValueError("dbms 필수 — 호출자(phase_ast)가 ctx.target 으로 전달")
        if not name_case:
            raise ValueError("name_case 필수 — service 진입점에서 검증된 값 전달")
        # own_schema 는 빈값 허용 — schema_resolver 위계로 다음 fallback 단계 (DDL 매칭) 로 떨어짐.
        self.dbms = dbms.lower()
        self.own_schema = (own_schema or "").strip()
        self._name_to_schema = name_to_schema
        self._ddl_table_metadata = ddl_table_metadata or {}
        # Phase 0 에서 빌드된 ddl_table_index + ddl_pattern (Phase 2B dml_extract 의 ddl_context 주입에 사용)
        self._ddl_table_index = ddl_table_index or {}
        self._ddl_pattern = ddl_pattern
        self.name_case = name_case.lower()
        self.datasource_name = (datasource_name or "").strip()

        self._table_summary_store: Dict[Tuple[str, str], Dict[str, Any]] = {}

    def _apply_name_case(self, name: str) -> str:
        """대소문자 변환 적용"""
        if not name:
            return name
        if self.name_case == "uppercase":
            return name.upper()
        elif self.name_case == "lowercase":
            return name.lower()
        return name

    def _get_effective_schema(self, schema: Optional[str], table_name: str = "") -> str:
        """schema 결정 위계 (모든 전략 공통).

        1) explicit schema (LLM 명시) → 그거
        2) name_to_schema lookup (DDL 단일 매칭) → 그거
        3) own_schema (PROCEDURE 자기 schema) → 그거
        4) "" (빈값 — 강제 'public' X)
        """
        return resolve_schema(
            explicit_schema=schema or "",
            table_name=table_name,
            name_to_schema=self._name_to_schema,
            own_schema=self.own_schema,
        )

    def _get_ddl_metadata(self, schema: Optional[str], table_name: str) -> Tuple[Dict[str, Any], bool]:
        """DDL 메타데이터 조회 및 대소문자 변환 여부 결정"""
        effective_schema = self._get_effective_schema(schema, table_name)
        if not effective_schema or not table_name:
            return {}, False
        ddl_lookup_key = (effective_schema.lower(), table_name.lower())
        ddl_meta = self._ddl_table_metadata.get(ddl_lookup_key, {})
        skip_case_conversion = bool(ddl_meta and ddl_meta.get('original_name'))
        return ddl_meta, skip_case_conversion

    def _resolve_table_name_with_ddl(self, schema_raw: Optional[str], name_raw: str) -> Tuple[str, str, bool]:
        """DDL 캐시를 고려하여 테이블명 정규화"""
        ddl_meta, skip_case_conversion = self._get_ddl_metadata(schema_raw, name_raw)
        effective_schema = self._get_effective_schema(schema_raw, name_raw)

        if skip_case_conversion:
            schema_part = ddl_meta.get('original_schema', self._apply_name_case(effective_schema))
            name_part = ddl_meta.get('original_name')
        else:
            schema_part = self._apply_name_case(effective_schema)
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
        ancestor_tree: str,
        sibling_brief: str,
        node_label: str,
    ) -> Tuple[str, str]:
        """v5.0 — 디렉토리 형식 한 줄 → rules/dbms/node_context.yaml"""
        def _call():
            inputs = {
                "node_label": node_label,
                "skeleton_code": skeleton_code,
                "locale": self.locale,
            }
            if ancestor_tree:
                inputs["ancestor_tree"] = ancestor_tree
            if sibling_brief:
                inputs["sibling_brief"] = sibling_brief
            result = prompts.execute_raw("dbms", "node_context", inputs, self.api_key)
            if not isinstance(result, dict):
                raise ValueError(f"node_context 규칙이 dict가 아닌 값을 반환: {type(result)}")
            parent_ctx = (result.get("parent_context") or "").strip()
            sibling_ctx = (result.get("sibling_context") or "").strip()
            if not parent_ctx:
                raise ValueError("node_context 규칙이 빈 parent_context를 반환")
            return parent_ctx, sibling_ctx
        return await asyncio.to_thread(_call)

    def _node_match_name(self, node: StatementNode) -> str:
        """노드의 canonical match name — MERGE 키와 MATCH 키 양쪽에서 공유.

        이 함수가 단일 source of truth: `_static_node_item` 이 MERGE 시
        `name` 으로 SET 하고, 관계 빌더 (`_build_has_procedure_query` /
        `_build_parent_of_query` / `_build_next_query`) 가 같은 이름으로 MATCH.
        같은 라인 같은 라벨 형제 노드 (예: 함수 파라미터 ``f(a, b)``) 도 name 차이로
        정확히 1:1 매치 → 카테시안 곱 방지.

        - FILE → 파일명
        - PROCEDURE/FUNCTION/TRIGGER → unit_name (실제 이름)
        - VARIABLE/PACKAGE_VARIABLE/CONSTANT_FIELD/PARAMETER → 실제 이름
        - 기타 (SELECT, BEGIN, RETURN 등) → ``build_statement_name`` 으로 ``<TYPE>[<startLine>]``
        """
        label = node.node_type
        if label == "FILE":
            return self.file_name
        if label in PROCEDURE_TYPES and node.unit_name:
            return node.unit_name
        if label in ("VARIABLE", "PACKAGE_VARIABLE", "CONSTANT_FIELD", "PARAMETER") and node.name:
            return node.name
        return build_statement_name(label, node.start_line)

    def _static_node_item(self, node: StatementNode) -> Dict[str, Any]:
        """정적 노드 1건의 UNWIND 입력 dict.

        ``build_static_node_queries`` 가 label 별 그룹 → ``MERGE (n:{label} {name, directory, file_name})``
        + ``SET n.startLine = row.start_line, n += row.props`` 로 일괄 처리.

        MERGE 키는 (label, name, directory, file_name) 단일 정책 — node_name 이 모든 노드의 unique 식별자.
        """
        label = node.node_type
        # FILE 노드는 framework 의 MODULE 격 — frontend Navigator 가 PACKAGE 트리에서
        # 인식하려면 :MODULE 라벨이 필요. Cypher 의 멀티라벨로 양쪽 의미 보존.
        if label == "FILE":
            label = "MODULE:FILE"

        props: Dict[str, Any] = {
            "endLine":      node.end_line,
            "node_code":    node.code,
            "token":        node.token,
            "has_children": node.has_children,
        }
        if node.signature:
            props["signature"] = node.signature
        if node.parameters:
            props["parameters"] = node.parameters
        if node.variable_type:
            props["variable_type"] = node.variable_type
        if node.init_value:
            props["init_value"] = node.init_value
        if node.modifiers:
            props["modifiers"] = node.modifiers

        if node.node_type in PROCEDURE_TYPES and node.unit_name:
            props["procedure_name"] = node.unit_name
            props["procedure_type"] = node.node_type
            if node.schema_name:
                props["schema_name"] = node.schema_name
        elif node.unit_name:
            props["procedure_name"] = node.unit_name
            if node.schema_name:
                props["schema_name"] = node.schema_name

        if node.has_children:
            props["summarized_code"] = node.get_placeholder_code()

        return {
            "label":      label,
            "node_name":  self._node_match_name(node),
            "directory":  self.full_directory,
            "file_name":  self.file_name,
            "start_line": node.start_line,
            "props":      props,
        }

    def _build_relationship_queries(self) -> List[Union[str, Dict[str, Any]]]:
        """정적 관계 쿼리 생성 (HAS_PROCEDURE / PARENT_OF / NEXT) — UNWIND 통합.

        items 누적 → 종류별 `build_X_queries` 1회 호출 → label 쌍별 1 UNWIND.
        라운드트립: ~3N → ~ (label 쌍 수) 단위 (수십배 절감).
        """
        has_procedure_items: List[Dict[str, Any]] = []
        parent_of_items: List[Dict[str, Any]] = []
        next_items: List[Dict[str, Any]] = []

        for node in self._nodes or []:
            for child in node.children:
                if node.node_type == "FILE" and child.node_type in PROCEDURE_TYPES:
                    has_procedure_items.append(self._has_procedure_item(node, child))
                else:
                    parent_of_items.append(self._parent_of_item(node, child))

            prev = None
            for child in node.children:
                if prev:
                    next_items.append(self._next_item(prev, child))
                prev = child

        queries: List[Union[str, Dict[str, Any]]] = []
        queries.extend(build_has_procedure_queries(has_procedure_items))
        queries.extend(build_parent_of_queries(parent_of_items))
        queries.extend(build_next_queries(next_items))
        return queries

    def _next_item(self, prev: "StatementNode", current: "StatementNode") -> Dict[str, Any]:
        """NEXT 관계용 row item — MATCH 키 = `_node_match_name(node)` (MERGE 정책 일관)."""
        return {
            "prev_type": prev.node_type,
            "prev_name": self._node_match_name(prev),
            "curr_type": current.node_type,
            "curr_name": self._node_match_name(current),
            "directory": self.full_directory,
            "file_name": self.file_name,
        }

    async def _invoke_llm(self, batch: AnalysisBatch) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
        """LLM 호출 (일반 분석 + DML 분석)"""
        # 조건부 플래그 계산 — 프롬프트의 {% if %} 블록과 매핑 (docs/prompt-contract.md 참조)
        has_dml = bool(batch.dml_ranges)
        is_unit_root = any(n.node_type in ("PROCEDURE", "FUNCTION", "TRIGGER") for n in batch.nodes)

        general_task = None
        general_code = ""
        if batch.ranges:
            general_code, context = batch.build_payload()
            # Bottom-up Rule 처리용 노드별 메타데이터
            node_metadata = batch.build_node_metadata()

        table_task = None
        dml_payload = batch.build_dml_payload()
        dml_code = dml_payload[0] if dml_payload and batch.dml_ranges else ""

        # DDL 결정론 매칭 (라인 매핑 포함) — batch 의 합집합 코드에서 1회 매칭.
        # 사용 라인 매핑이 ddl_context 에 포함되어 LLM 이 자기 range 의 테이블을 자동 식별.
        # → node_analysis: 자기 range 외 테이블은 LLM 이 스스로 무시 + dtype 정확도
        # → dml_extract: 식별 작업 skip + 사용 의미·R/W 판단만
        ddl_context = ""
        if self._ddl_pattern and (general_code or dml_code):
            matched_with_lines = ddl_table_matcher.match_in_code_with_lines(
                general_code or dml_code, self._ddl_pattern,
            )
            ddl_context = ddl_table_matcher.build_context(matched_with_lines, self._ddl_table_index)

        # enclosing PROCEDURE/FUNCTION 의 parameters 텍스트 — LLM 의미 풀이용
        function_parameters = _extract_enclosing_parameters(batch.nodes)
        # 참조된 enclosing 변수들의 (name, type, init_value) — REFERENCES + initValue 컨텍스트
        referenced_variables = _extract_referenced_variables(batch.nodes)

        if batch.ranges:
            def _call_analyze_code():
                inputs = {
                    "code": general_code,
                    "ranges": batch.ranges,
                    "count": len(batch.ranges),
                    "locale": self.locale,
                    "node_metadata": node_metadata,
                }
                if context.strip():
                    inputs["context"] = context
                if ddl_context:
                    inputs["ddl_context"] = ddl_context
                if function_parameters:
                    inputs["function_parameters"] = function_parameters
                if referenced_variables:
                    inputs["referenced_variables"] = referenced_variables
                if has_dml:
                    inputs["has_dml"] = True
                if is_unit_root:
                    inputs["is_unit_root"] = True
                return prompts.execute_raw("dbms", "node_analysis", inputs, self.api_key)
            general_task = asyncio.to_thread(_call_analyze_code)

        if dml_payload and batch.dml_ranges:
            _dml_code, dml_context = dml_payload
            def _call_analyze_dml():
                inputs = {"code": _dml_code, "ranges": batch.dml_ranges, "locale": self.locale}
                if dml_context.strip():
                    inputs["context"] = dml_context
                if ddl_context:
                    inputs["ddl_context"] = ddl_context
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
        """LLM 분석 결과를 쿼리로 변환 — summary/table/column/dblink/fk 모두 UNWIND."""
        queries: List[Union[str, Dict[str, Any]]] = []

        # --- 결과 검증 ---
        if not isinstance(llm_result, tuple):
            raise RuntimeError(f"배치#{batch.batch_id} llm_result가 tuple이 아님: {type(llm_result).__name__}")

        general_result, table_result = llm_result

        # --- Summary 처리 — items 누적 후 UNWIND 1회 ---
        general_result = self.validate_dict_result(
            general_result, "general_result", batch.batch_id, allow_none=True
        )
        if general_result:
            analysis_list = general_result.get("analysis") or []
            summary_items: List[Dict[str, Any]] = []
            for node, analysis in zip(batch.nodes, analysis_list):
                if not analysis:
                    continue

                summary = analysis.get("summary") or ""
                if not summary:
                    continue
                # actor 는 _apply_summary_to_nodes 에서 node.actor 로 저장됨 →
                # phase_summary 가 unit-root (FUNCTION/PROCEDURE/TRIGGER) 에 일괄 부착.
                # node_name 은 _node_match_name 단일 정책 사용 (MERGE 키와 일관).
                summary_items.append({
                    "node_type":      node.node_type,
                    "start_line":     node.start_line,
                    "end_line":       node.end_line,
                    "directory":      self.full_directory,
                    "file_name":      self.file_name,
                    "node_name":      self._node_match_name(node),
                    "summary":        str(summary),
                    "node_code":      node.code,
                    "token":          node.token,
                    "procedure_name": node.unit_name or "",
                    "has_children":   node.has_children,
                })

                # unit_summary_store 에는 리프 노드만 저장 (부모 summary 는 자식 포함이라 중복 제거)
                if unit_summary_store is not None and node.unit_key and node.unit_key in unit_summary_store:
                    if not node.has_children:
                        key = f"{node.node_type}_{node.start_line}_{node.end_line}"
                        unit_summary_store[node.unit_key][key] = summary

            queries.extend(build_summary_update_queries(summary_items))

        # --- 테이블 처리 ---
        table_result = self.validate_dict_result(
            table_result, "table_result", batch.batch_id, allow_none=True
        )
        if table_result:
            queries.extend(self._build_table_queries(batch, table_result))

        return queries

    # =========================================================================
    # DBMS 전용 메서드
    # =========================================================================

    def _build_table_queries(
        self,
        batch: AnalysisBatch,
        table_result: Dict[str, Any]
    ) -> List[Union[str, Dict[str, Any]]]:
        """DML 테이블 분석 결과를 UNWIND 쿼리로 변환 — items 누적 후 종류별 1쿼리.

        종류:
          - CREATE_TEMP_TABLE → temp_items
          - DML 노드 → :Table 관계 → dml_items (read/write 그룹)
          - 컬럼 → column_items
          - DBLink → dblink_items
          - FK → fk_items (별도 함수에서 누적)
        """
        node_map: Dict[Tuple[int, int], StatementNode] = {
            (node.start_line, node.end_line): node for node in batch.nodes
        }
        ranges = table_result.get('ranges', []) or []

        temp_items: List[Dict[str, Any]] = []
        dml_items: List[Dict[str, Any]] = []
        column_items: List[Dict[str, Any]] = []
        dblink_items: List[Dict[str, Any]] = []
        fk_table_items: List[Dict[str, Any]] = []
        fk_column_items: List[Dict[str, Any]] = []

        for range_entry in ranges:
            try:
                start_line = int(range_entry.get('startLine'))
                end_line = int(range_entry.get('endLine'))
            except (TypeError, ValueError) as e:
                raise RuntimeError(f"LLM 테이블 분석 결과에 잘못된 라인 번호: startLine={range_entry.get('startLine')}, endLine={range_entry.get('endLine')}") from e

            node = node_map.get((start_line, end_line))
            if not node:
                continue

            tables = range_entry.get('tables', []) or []

            if node.node_type == 'CREATE_TEMP_TABLE':
                self._collect_create_temp_table_items(node, tables, temp_items)
                continue

            self._collect_dml_table_items(node, tables, dml_items, column_items)
            self._collect_dblink_items(node, range_entry.get('dbLinks', []) or [], dblink_items)
            self._collect_fk_items(range_entry.get('fkRelations', []) or [], fk_table_items, fk_column_items)

        queries: List[Union[str, Dict[str, Any]]] = []
        queries.extend(build_create_temp_table_queries(temp_items))
        queries.extend(build_dml_table_relation_queries(dml_items))
        queries.extend(build_column_queries(column_items))
        queries.extend(build_dblink_queries(dblink_items))
        queries.extend(build_fk_to_table_queries(fk_table_items))
        queries.extend(build_fk_to_column_queries(fk_column_items))
        return queries

    def _collect_create_temp_table_items(
        self,
        node: StatementNode,
        tables: List[Dict[str, Any]],
        out: List[Dict[str, Any]],
    ) -> None:
        for entry in tables:
            table_name = (entry.get('table') or '').strip()
            if not table_name:
                continue
            schema_part_raw, name_part_raw, _ = parse_table_identifier(table_name)
            schema_part = self._apply_name_case(self._get_effective_schema(schema_part_raw, name_part_raw))
            name_part = self._apply_name_case(name_part_raw)
            out.append({
                "node_type":  node.node_type,
                "start_line": node.start_line,
                "directory":  self.full_directory,
                "file_name":  self.file_name,
                "name":       name_part,
                "schema":     schema_part,
                "db":         self.dbms,
            })

    def _collect_dml_table_items(
        self,
        node: StatementNode,
        tables: List[Dict[str, Any]],
        dml_out: List[Dict[str, Any]],
        column_out: List[Dict[str, Any]],
    ) -> None:
        for entry in tables:
            table_name = (entry.get('table') or '').strip()
            if not table_name:
                continue

            schema_part_raw, name_part_raw, db_link_value = parse_table_identifier(table_name)
            schema_part, name_part, skip_case_conversion = self._resolve_table_name_with_ddl(schema_part_raw, name_part_raw)
            effective_schema = self._get_effective_schema(schema_part, name_part)
            if not skip_case_conversion:
                effective_schema = self._apply_name_case(effective_schema)
                converted_name = self._apply_name_case(name_part)
            else:
                converted_name = name_part

            access_mode_raw = entry.get('accessMode') or entry.get('mode')
            if not access_mode_raw:
                raise RuntimeError(
                    f"LLM dml_extract 결과에 accessMode/mode 누락 | table={table_name} entry={entry}"
                )
            access_mode = access_mode_raw.lower()
            rel_types = [TABLE_RELATIONSHIP_MAP.get(mode, mode.upper()) for mode in ['r', 'w'] if mode in access_mode]

            table_desc_raw = entry.get('tableDescription') or entry.get('description') or ''
            bucket_key = self._record_table_summary(schema_part, name_part, table_desc_raw, skip_case_conversion=skip_case_conversion)

            dml_out.append({
                "node_type":   node.node_type,
                "start_line":  node.start_line,
                "directory":   self.full_directory,
                "file_name":   self.file_name,
                "db":          self.dbms,
                "schema_name": effective_schema,
                "table_name":  converted_name,
                "rel_types":   rel_types,
                "db_link":     db_link_value or None,
            })

            self._collect_column_items(
                entry.get('columns', []) or [],
                converted_name,
                effective_schema,
                bucket_key,
                column_out,
            )

    def _collect_column_items(
        self,
        columns: List[Dict[str, Any]],
        converted_table_name: str,
        effective_schema: str,
        bucket_key: Tuple[str, str],
        out: List[Dict[str, Any]],
    ) -> None:
        ddl_meta, _ = self._get_ddl_metadata(effective_schema, converted_table_name)
        ddl_columns = ddl_meta.get('columns', {}) if ddl_meta else {}

        for column in columns:
            column_name_raw = (column.get('name') or '').strip()
            if not column_name_raw:
                continue
            column_name = self._resolve_column_name_with_ddl(column_name_raw, ddl_columns)
            raw_dtype = column.get('dtype') or ''
            raw_column_desc = (column.get('description') or column.get('comment') or '').strip()
            nullable = column.get('nullable', True)

            self._record_column_summary(
                bucket_key,
                column_name,
                raw_column_desc,
                dtype=raw_dtype,
                nullable=nullable,
                examples=column.get('examples') or [],
            )

            # DDL 과 fqn 형식 일치: datasource.schema.table.column (DDL handler 와 동일 규칙)
            fqn = '.'.join(filter(None, [self.datasource_name, effective_schema, converted_table_name, column_name])).lower()

            out.append({
                "db":          self.dbms,
                "schema_name": effective_schema,
                "table_name":  converted_table_name,
                "fqn":         fqn,
                "col_name":    column_name,
                "col_type":    raw_dtype,
                "col_desc":    raw_column_desc,
                "nullable":    nullable,
            })

    def _collect_dblink_items(
        self,
        node: StatementNode,
        db_links: List[Dict[str, Any]],
        out: List[Dict[str, Any]],
    ) -> None:
        """DBLink 처리. mode 누락 시 fail-fast (LLM 응답 검증)."""
        for link_item in db_links:
            link_name_raw = (link_item.get('name') or '').strip()
            if not link_name_raw:
                continue
            mode_raw = link_item.get('mode')
            if not mode_raw:
                raise RuntimeError(
                    f"LLM dml_extract 결과에 dbLink mode 누락 | link={link_name_raw} item={link_item}"
                )
            mode = mode_raw.lower()
            schema_link_raw, name_link_raw, link_name = parse_table_identifier(link_name_raw)
            schema_link = self._apply_name_case(self._get_effective_schema(schema_link_raw, name_link_raw))
            name_link = self._apply_name_case(name_link_raw)
            out.append({
                "node_type":   node.node_type,
                "start_line":  node.start_line,
                "directory":   self.full_directory,
                "file_name":   self.file_name,
                "db":          self.dbms,
                "schema_name": schema_link,
                "table_name":  name_link,
                "link_name":   link_name,
                "mode":        mode,
            })

    def _collect_fk_items(
        self,
        fk_relations: List[Dict[str, Any]],
        fk_table_out: List[Dict[str, Any]],
        fk_column_out: List[Dict[str, Any]],
    ) -> None:
        """FK 관계 → FK_TO_TABLE / FK_TO_COLUMN items 누적."""
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

            effective_src_schema = src_schema or self._apply_name_case(
                self._get_effective_schema("", src_name)
            )
            effective_tgt_schema = tgt_schema or self._apply_name_case(
                self._get_effective_schema("", tgt_name)
            )

            for src_col, tgt_col in zip(src_columns, tgt_columns):
                converted_src_col = self._apply_name_case(src_col)
                converted_tgt_col = self._apply_name_case(tgt_col)

                fk_table_out.append({
                    "src_schema": effective_src_schema,
                    "src_name":   src_name,
                    "tgt_schema": effective_tgt_schema,
                    "tgt_name":   tgt_name,
                    "db":         self.dbms,
                    "src_col":    converted_src_col,
                    "tgt_col":    converted_tgt_col,
                })

                src_fqn = '.'.join(filter(None, [self.datasource_name, effective_src_schema, src_name, converted_src_col])).lower()
                tgt_fqn = '.'.join(filter(None, [self.datasource_name, effective_tgt_schema, tgt_name, converted_tgt_col])).lower()

                fk_column_out.append({"src_fqn": src_fqn, "tgt_fqn": tgt_fqn})

    def _record_table_summary(self, schema: Optional[str], name: str, description: Optional[str], skip_case_conversion: bool = False) -> Tuple[str, str]:
        """테이블 설명 누적"""
        effective_schema = self._get_effective_schema(schema, name)

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
