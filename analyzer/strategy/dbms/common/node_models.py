"""DBMS 전략 데이터 모델 통합

4개 소스에서 추출한 모델 클래스들을 하나의 모듈로 통합.
- StatementNode: AST 노드 (base/statement_node.py)
- AnalysisBatch, BatchPlanner: 배치 처리 (base/batch.py)
- FileStatus, FileAnalysisContext: 파일 상태 (base/file_context.py)
- ProcedureInfo: 프로시저 정보 (_ast_core.py)
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple, Set

from config.settings import settings
from util.logger import log_process
from util.code_text_utils import calculate_code_token

# 설정에서 가져오는 상수
MAX_CONTEXT_TOKEN = settings.batch.max_context_token
MAX_BATCH_TOKEN = settings.batch.max_batch_token


# ==================== StatementNode ====================

@dataclass(slots=True)
class StatementNode:
    """평탄화된 AST 노드를 표현합니다.

    - 수집 단계에서 모든 노드를 생성합니다.
    - 이후 배치가 만들어질 때 이 객체를 그대로 사용합니다.
    - LLM 요약이 끝나면 `summary`와 `completion_event`가 채워집니다.
    - `ok` 플래그로 성공 여부를 추적합니다 (자식 실패 시 부모도 False).
    - `context`는 부모 컨텍스트 추출 결과를 저장합니다.

    통합 필드:
    - unit_key: 프로시저/클래스 고유키 (procedure_key 또는 class_key)
    - unit_name: 프로시저/클래스 이름 (procedure_name 또는 class_name)
    - unit_kind: 프로시저/클래스 타입 (procedure_type 또는 class_kind)
    """
    # 기본 필드
    node_id: int
    start_line: int
    end_line: int
    node_type: str
    code: str
    token: int
    has_children: bool
    analyzable: bool

    # 통합 필드 (전략 공통)
    unit_key: Optional[str]      # procedure_key 또는 class_key
    unit_name: Optional[str]     # procedure_name 또는 class_name
    unit_kind: Optional[str]     # procedure_type 또는 class_kind

    # 전략별 확장 필드 (선택적)
    schema_name: Optional[str] = None   # DBMS 전용: 스키마 이름
    dml: bool = False                   # DBMS 전용: DML 문장 여부
    package_name: Optional[str] = None  # Framework 전용: 패키지명 (com.example.service)

    # AST JSON에서 추출한 메타데이터 (선택적)
    name: Optional[str] = None            # 노드 이름 (ANTLR name 속성)
    signature: Optional[str] = None        # 함수/프로시저 시그니처
    modifiers: Optional[str] = None        # public, private, static 등 수정자
    return_type: Optional[str] = None      # 반환 타입
    parameters: Optional[str] = None       # 파라미터 문자열
    generic_type: Optional[str] = None     # 제네릭 타입
    extends_type: Optional[str] = None     # 상속 타입 (extends)
    implements_types: Optional[str] = None # 구현 인터페이스 (implements)
    field_type: Optional[str] = None       # 필드 타입

    # Framework 전용: 어노테이션 정보
    annotations: Optional[str] = None         # 어노테이션 문자열 (예: "@Entity @Table(name='orders')")

    # Framework 전용: 주석 정보
    comment: Optional[str] = None             # Javadoc/주석 (ANTLR comment 속성)

    # 코드 라인 정보
    lines: List[Tuple[int, str]] = field(default_factory=list)

    # 부모-자식 관계
    parent: Optional["StatementNode"] = None
    children: List["StatementNode"] = field(default_factory=list)

    # LLM 분석 결과
    summary: Optional[str] = None
    context: Optional[str] = None  # 부모 컨텍스트 (자식 분석 시 전달됨)
    sibling_context: Optional[str] = None  # 이전 형제 컨텍스트
    ok: bool = True  # LLM 분석 성공 여부 (자식 실패 시 부모도 False)

    # Bottom-up 누적 결과 — 노드별 Rule/Example/Question 등을 담는 map
    # 예: {"rules": [...], "questions": [...]}
    # 구조 래퍼: 자식 것 그대로 통과
    # 의미 부모: 자식 것 받아 자기 조건 합침
    # 리프: 자기 business 가 있으면 생성, 없으면 빈 배열/없음
    analysis_map: Dict[str, Any] = field(default_factory=dict)

    # antlr-parser 의 initializerContainsMethodCall 플래그 매핑
    # ASSIGNMENT/IF/ELSIF/RETURN 등의 표현식에 함수 호출 존재 여부
    initializer_contains_method_call: bool = False

    # 비동기 이벤트
    completion_event: asyncio.Event = field(init=False, repr=False)
    context_ready_event: asyncio.Event = field(init=False, repr=False)

    def __post_init__(self):
        object.__setattr__(self, "completion_event", asyncio.Event())
        object.__setattr__(self, "context_ready_event", asyncio.Event())

    def get_raw_code(self) -> str:
        """라인 번호를 포함하여 노드의 원문 코드를 반환합니다."""
        return '\n'.join(f"{line_no}: {text}" for line_no, text in self.lines)

    def is_structural(self) -> bool:
        """Bottom-up Rule 처리에서 구조 래퍼 (자식 rules 통과) 인지 판정.

        구조 래퍼: PROCEDURE/FUNCTION/TRIGGER/BEGIN/FILE 등 — 조건 없는 껍데기.
        나머지는 전부 rule_target (의미 부모 or 리프) 로 취급됨.
        """
        from analyzer.strategy.dbms.common.analysis_constants import STRUCTURAL_NODE_TYPES
        return self.node_type in STRUCTURAL_NODE_TYPES

    def get_compact_code(self) -> str:
        """자식 요약을 포함한 부모 코드(LLM 입력용)를 생성합니다.

        DBMS와 Framework 공통 로직:
        - 자식 이전의 부모 고유 코드를 그대로 복사
        - 자식 구간은 자식 요약으로 대체 (없으면 원문 보관)
        - 마지막 자식 이후 부모 코드 추가
        """
        if not self.children:
            return self.code  # 원본과 동일하게 self.code 반환

        # VARIABLE 노드는 분석 대상이 아니므로 compact 코드 생성에서 제외
        filtered_children = [c for c in self.children if c.node_type != "VARIABLE"]
        if not filtered_children:
            return self.code

        result_lines: List[str] = []
        line_index = 0
        total_lines = len(self.lines)
        sorted_children = sorted(filtered_children, key=lambda child: child.start_line)

        for child in sorted_children:
            # 자식 이전의 부모 고유 코드를 그대로 복사합니다.
            while line_index < total_lines and self.lines[line_index][0] < child.start_line:
                line_no, text = self.lines[line_index]
                result_lines.append(f"{line_no}: {text}")
                line_index += 1

            # 자식 구간은 자식 요약으로 대체합니다 (없으면 원문 보관).
            if child.summary:
                child_summary = child.summary.strip()
                summary_line = f"{child.start_line}~{child.end_line}: {child_summary}"
            else:
                log_process("PHASE:1", "COLLECT", f"⚠️ 부모 {self.start_line}~{self.end_line}의 자식 {child.start_line}~{child.end_line} (type={child.node_type}, analyzable={child.analyzable}) 요약 없음 - 원문 보관")
                summary_line = '\n'.join(
                    f"{line_no}: {text}"
                    for line_no, text in child.lines
                ).strip()

            result_lines.append(summary_line)

            # 자식 구간 원본 코드는 건너뜁니다.
            while line_index < total_lines and self.lines[line_index][0] <= child.end_line:
                line_index += 1

        # 마지막 자식 이후 부모 코드가 남아 있다면 추가합니다.
        while line_index < total_lines:
            line_no, text = self.lines[line_index]
            result_lines.append(f"{line_no}: {text}")
            line_index += 1

        return '\n'.join(result_lines)

    def get_deep_compact_code(self) -> str:
        """리프 노드만 summary로 대체하고, 중간 노드는 스켈레톤을 유지하는 재귀 compact code.

        get_compact_code()는 직계 자식만 처리하지만,
        이 메서드는 모든 depth의 리프 노드까지 재귀적으로 summary를 적용합니다.
        중간 노드(IF/LOOP/BEGIN)는 원문 스켈레톤이 유지되어 제어 흐름이 보존됩니다.
        """
        filtered_children = [c for c in self.children if c.node_type != "VARIABLE"]
        if not filtered_children:
            # 리프 노드: summary가 있으면 summary, 없으면 원문
            if self.summary:
                return f"{self.start_line}~{self.end_line}: {self.summary.strip()}"
            return self.get_raw_code()

        result_lines: List[str] = []
        line_index = 0
        total_lines = len(self.lines)
        sorted_children = sorted(filtered_children, key=lambda child: child.start_line)

        for child in sorted_children:
            while line_index < total_lines and self.lines[line_index][0] < child.start_line:
                line_no, text = self.lines[line_index]
                result_lines.append(f"{line_no}: {text}")
                line_index += 1

            # 자식도 재귀적으로 deep_compact_code 적용
            result_lines.append(child.get_deep_compact_code())

            while line_index < total_lines and self.lines[line_index][0] <= child.end_line:
                line_index += 1

        while line_index < total_lines:
            line_no, text = self.lines[line_index]
            result_lines.append(f"{line_no}: {text}")
            line_index += 1

        return '\n'.join(result_lines)

    def get_placeholder_code(self, preserve_types: Optional[Set[str]] = None, include_assigns: bool = False) -> str:
        """자식 구간을 placeholder로 치환한 코드를 반환합니다.

        Args:
            preserve_types: 원문 유지할 노드 타입 Set (예: {"EXTENDS", "IMPLEMENTS", "METHOD"})
            include_assigns: True이면 ASSIGNMENT/NEW_INSTANCE 노드를 재귀적으로 찾아서 원문 유지
        """
        if not self.children:
            return self.get_raw_code()

        preserve_types = preserve_types or set()

        # include_assigns=True이면 ASSIGNMENT/NEW_INSTANCE를 재귀적으로 수집
        assign_node_set: Set[Tuple[int, int]] = set()
        if include_assigns:
            ASSIGN_TYPES = {"ASSIGNMENT", "NEW_INSTANCE"}

            def find_assign_nodes_recursive(node: "StatementNode") -> List["StatementNode"]:
                """재귀적으로 ASSIGNMENT, NEW_INSTANCE 노드를 수집합니다."""
                results = []
                for child in node.children:
                    if child.node_type in ASSIGN_TYPES:
                        results.append(child)
                    results.extend(find_assign_nodes_recursive(child))
                return results

            assign_nodes = find_assign_nodes_recursive(self)
            assign_node_set = {(n.start_line, n.end_line) for n in assign_nodes}

        # VARIABLE 노드는 placeholder 생성 시 무시 (부모 코드만 유지)
        filtered_children = [c for c in self.children if c.node_type != "VARIABLE"]
        if not filtered_children:
            return self.get_raw_code()

        result_lines: List[str] = []
        line_index = 0
        total_lines = len(self.lines)
        sorted_children = sorted(filtered_children, key=lambda child: child.start_line)

        for child in sorted_children:
            # 자식 이전의 부모 코드를 그대로 출력
            while line_index < total_lines and self.lines[line_index][0] < child.start_line:
                line_no, text = self.lines[line_index]
                result_lines.append(f"{line_no}: {text}")
                line_index += 1

            # 원문 유지할 노드: preserve_types에 포함되거나 ASSIGNMENT/NEW_INSTANCE
            child_span = (child.start_line, child.end_line)
            should_preserve = (
                child.node_type in preserve_types or
                (include_assigns and child_span in assign_node_set)
            )

            if should_preserve:
                # 원문 그대로 출력
                while line_index < total_lines and self.lines[line_index][0] <= child.end_line:
                    line_no, text = self.lines[line_index]
                    result_lines.append(f"{line_no}: {text}")
                    line_index += 1
            else:
                # 나머지 자식은 ...code...로 치환
                result_lines.append(f"{child.start_line}: ...code...")
                while line_index < total_lines and self.lines[line_index][0] <= child.end_line:
                    line_index += 1

        # 마지막 자식 이후 부모 코드가 남아 있다면 추가
        while line_index < total_lines:
            line_no, text = self.lines[line_index]
            result_lines.append(f"{line_no}: {text}")
            line_index += 1

        return '\n'.join(result_lines)

    def get_skeleton_code(self) -> str:
        """자식 구간을 .... 로 압축한 스켈레톤 코드를 반환합니다.

        연속된 자식 구간은 하나의 .... 로 압축됩니다.
        부모 컨텍스트 추출용으로 사용됩니다.
        """
        if not self.children:
            return self.get_raw_code()

        # VARIABLE 노드는 스켈레톤 생성에서 제외 (컨텍스트 노이즈 감소)
        filtered_children = [c for c in self.children if c.node_type != "VARIABLE"]
        if not filtered_children:
            return self.get_raw_code()

        result_lines: List[str] = []
        sorted_children = sorted(filtered_children, key=lambda child: child.start_line)
        in_child_block = False

        for line_no, text in self.lines:
            is_child_line = any(
                child.start_line <= line_no <= child.end_line
                for child in sorted_children
            )

            if is_child_line:
                if not in_child_block:
                    result_lines.append("    ....")
                    in_child_block = True
                # 연속된 자식 라인은 스킵
            else:
                in_child_block = False
                result_lines.append(f"{line_no}: {text}")

        return '\n'.join(result_lines)

    def get_ancestor_context(self, max_tokens: int) -> str:
        """조상 노드들의 컨텍스트를 결합하여 반환합니다.

        부모 컨텍스트와 형제 컨텍스트를 모두 포함합니다.
        가장 가까운 조상부터 토큰 상한까지 누적합니다.

        Args:
            max_tokens: 최대 토큰 수
        """
        if not self.parent:
            return ""

        context_parts: List[str] = []
        remaining = max_tokens
        current = self.parent

        while current and remaining > 0:
            # 부모의 context가 있으면 사용 (LLM이 생성한 핵심 컨텍스트)
            if current.context:
                ctx_tokens = calculate_code_token(current.context)
                if ctx_tokens <= remaining:
                    context_parts.insert(0, f"[부모] {current.context}")
                    remaining -= ctx_tokens
                else:
                    # 토큰 초과 시 중단
                    break
            # 형제 컨텍스트도 포함
            if current.sibling_context:
                sib_tokens = calculate_code_token(current.sibling_context)
                if sib_tokens <= remaining:
                    context_parts.insert(0, f"[형제] {current.sibling_context}")
                    remaining -= sib_tokens
            current = current.parent

        if not context_parts:
            return ""

        return "[CONTEXT]\n" + "\n---\n".join(context_parts) + "\n[/CONTEXT]\n"

    def needs_context_generation(self, excluded_types: Set[str]) -> bool:
        """이 노드가 컨텍스트 생성이 필요한 부모 노드인지 확인합니다.

        조건:
        - has_children = True (자식이 있음)
        - analyzable = True (분석 대상)
        - node_type이 excluded_types에 포함되지 않음

        Args:
            excluded_types: 제외할 노드 타입 (DBMS: PROCEDURE_TYPES, Framework: CLASS_TYPES)
        """
        return (
            self.has_children
            and self.analyzable
            and self.node_type not in excluded_types
        )

    def get_previous_sibling(self) -> Optional["StatementNode"]:
        """같은 부모의 이전 형제 노드를 반환합니다.

        startLine 기준으로 정렬하여 현재 노드 바로 앞의 형제를 찾습니다.

        Returns:
            이전 형제 노드 또는 None (첫 번째 자식이거나 부모가 없는 경우)
        """
        if not self.parent:
            return None

        siblings = self.parent.children
        if not siblings or len(siblings) < 2:
            return None

        # startLine 기준 정렬
        sorted_siblings = sorted(siblings, key=lambda n: n.start_line)

        # 현재 노드의 인덱스 찾기
        current_idx = -1
        for i, sib in enumerate(sorted_siblings):
            if sib.node_id == self.node_id:
                current_idx = i
                break

        if current_idx <= 0:
            return None

        return sorted_siblings[current_idx - 1]

    def get_sibling_code(self) -> str:
        """이전 형제 노드의 원문 코드를 반환합니다.

        Returns:
            이전 형제의 원문 코드 또는 빈 문자열
        """
        sibling = self.get_previous_sibling()
        if not sibling:
            return ""
        return sibling.get_raw_code()


# ==================== AnalysisBatch & BatchPlanner ====================

@dataclass(slots=True)
class AnalysisBatch:
    """분석 배치 정보.

    공통 필드:
    - batch_id: 배치 ID
    - nodes: 배치에 포함된 노드 리스트
    - ranges: 분석 범위 리스트
    - progress_line: 진행률 표시용 라인 번호

    DBMS 확장 필드:
    - dml_ranges: DML 범위 리스트 (DBMS 전용)
    """
    batch_id: int
    nodes: List[StatementNode]
    ranges: List[Dict[str, int]]
    progress_line: int

    # DBMS 전용 (선택적)
    dml_ranges: List[Dict[str, int]] = field(default_factory=list)

    def build_payload(self) -> Tuple[str, str]:
        """LLM 호출용 코드와 컨텍스트를 분리하여 반환합니다.

        Returns:
            (code, context) 튜플 - 코드와 컨텍스트를 분리
            code: 주석 + 어노테이션 + 코드 순서로 구성
        """
        code_parts: List[str] = []
        context_parts: List[str] = []

        for node in self.nodes:
            # 부모 컨텍스트 (변경 없음)
            context = node.get_ancestor_context(MAX_CONTEXT_TOKEN)
            if context:
                context_parts.append(context)
            else:
                context_parts.append("")

            # 코드 본문: 주석 + 어노테이션 + 코드
            code_lines = []

            # 1. 주석 추가
            if node.comment:
                code_lines.append(f"주석: {node.comment}")

            # 2. 어노테이션 추가
            if node.annotations:
                code_lines.append(f"어노테이션: {node.annotations}")

            # 3. 코드 추가
            code = node.get_compact_code()
            code_lines.append(code)

            code_parts.append('\n'.join(code_lines))

        return '\n\n'.join(code_parts), '\n\n'.join(context_parts)

    def build_node_metadata(self) -> List[Dict[str, Any]]:
        """배치 내 각 노드의 Bottom-up 관련 메타데이터.

        node_analysis 프롬프트가 i번째 코드 ↔ i번째 메타 매핑해 분기 렌더링.

        Returns:
            [{"node_category": "meaningful|leaf",
              "child_rules": [...], "child_questions": [...]}, ...]
            (structural 은 LLM 호출 스킵되므로 여기에 포함 안 됨)
        """
        metadata: List[Dict[str, Any]] = []
        for node in self.nodes:
            category = "meaningful" if node.has_children else "leaf"

            child_rules: List[dict] = []
            child_questions: List[dict] = []
            if node.has_children:
                for child in node.children:
                    cmap = child.analysis_map or {}
                    if cmap.get("rules"):
                        child_rules.extend(cmap["rules"])
                    if cmap.get("questions"):
                        child_questions.extend(cmap["questions"])

            metadata.append({
                "node_category": category,
                "child_rules": child_rules,
                "child_questions": child_questions,
            })
        return metadata

    def build_dml_payload(self) -> Optional[Tuple[str, str]]:
        """DML 노드만 추린 코드와 컨텍스트를 분리하여 반환합니다.

        DBMS 전용 메서드.

        Returns:
            (code, context) 튜플 또는 None - 코드와 컨텍스트를 분리
        """
        dml_nodes = [node for node in self.nodes if getattr(node, 'dml', False)]
        if not dml_nodes:
            return None

        code_parts: List[str] = []
        context_parts: List[str] = []

        for node in dml_nodes:
            code = node.get_compact_code() if node.has_children else node.get_raw_code()
            code_parts.append(code)

            context = node.get_ancestor_context(MAX_CONTEXT_TOKEN)
            if context:
                context_parts.append(context)
            else:
                context_parts.append("")

        return '\n\n'.join(code_parts), '\n\n'.join(context_parts)


class BatchPlanner:
    """수집된 노드를 토큰 한도 내에서 배치로 묶습니다."""

    def __init__(self, token_limit: int = MAX_BATCH_TOKEN):
        """토큰 한도를 지정하여 배치 생성기를 초기화합니다."""
        self.token_limit = token_limit

    def plan(
        self,
        nodes: List[StatementNode],
        include_dml_ranges: bool = False
    ) -> List[AnalysisBatch]:
        """토큰 한도를 넘지 않도록 노드를 분할하여 분석 배치를 생성합니다.

        Args:
            nodes: 분석 대상 노드 리스트
            include_dml_ranges: True이면 dml_ranges 계산 (DBMS용)

        Returns:
            배치 리스트
        """
        batches: List[AnalysisBatch] = []
        current_nodes: List[StatementNode] = []
        current_tokens = 0
        batch_id = 1

        for node in nodes:
            if not node.analyzable:
                continue

            # 부모 노드는 자식 요약이 준비된 후 단독으로 실행되므로 즉시 배치를 확정합니다.
            if node.has_children:
                # 현재까지 누적된 리프 배치를 먼저 확정합니다.
                if current_nodes:
                    log_process("PHASE:2", "BATCH", f"📦 배치 #{batch_id} 확정: 리프 노드 {len(current_nodes)}개 (토큰 {current_tokens}/{self.token_limit})")
                    batches.append(self._create_batch(batch_id, current_nodes, include_dml_ranges))
                    batch_id += 1
                    current_nodes = []
                    current_tokens = 0

                log_process("PHASE:2", "BATCH", f"📦 배치 #{batch_id} 확정: 부모 노드 단독 실행 (라인 {node.start_line}~{node.end_line}, 토큰 {node.token})")
                batches.append(self._create_batch(batch_id, [node], include_dml_ranges))
                batch_id += 1
                continue

            # 현재 배치가 토큰 한도를 초과한다면 쌓인 리프 노드들을 먼저 실행합니다.
            if current_nodes and current_tokens + node.token > self.token_limit:
                log_process("PHASE:2", "BATCH", f"📦 배치 #{batch_id} 확정: 토큰 한도 도달로 선 실행 (누적 {current_tokens}/{self.token_limit})")
                batches.append(self._create_batch(batch_id, current_nodes, include_dml_ranges))
                batch_id += 1
                current_nodes = []
                current_tokens = 0

            current_nodes.append(node)
            current_tokens += node.token

        if current_nodes:
            log_process("PHASE:2", "BATCH", f"📦 배치 #{batch_id} 확정: 마지막 리프 노드 {len(current_nodes)}개 (토큰 {current_tokens}/{self.token_limit})")
            batches.append(self._create_batch(batch_id, current_nodes, include_dml_ranges))

        return batches

    def _create_batch(
        self,
        batch_id: int,
        nodes: List[StatementNode],
        include_dml_ranges: bool = False
    ) -> AnalysisBatch:
        """배치 ID와 노드 리스트로 AnalysisBatch 객체를 생성합니다."""
        ranges = [{"startLine": node.start_line, "endLine": node.end_line} for node in nodes]
        progress = max(node.end_line for node in nodes)

        # DML 범위 계산 (DBMS용)
        dml_ranges = []
        if include_dml_ranges:
            dml_ranges = [
                {"startLine": node.start_line, "endLine": node.end_line, "type": node.node_type}
                for node in nodes
                if getattr(node, 'dml', False)
            ]

        return AnalysisBatch(
            batch_id=batch_id,
            nodes=nodes,
            ranges=ranges,
            progress_line=progress,
            dml_ranges=dml_ranges,
        )


# ==================== FileStatus & FileAnalysisContext ====================

class FileStatus(Enum):
    """파일 분석 상태

    상태 흐름:
    PENDING → PH1_OK → PH2_OK (정상)
    PENDING → PH1_FAIL (Phase 1 실패 → Phase 2 스킵)
    PENDING → PH1_OK → PH2_FAIL (Phase 2 실패)
    """
    PENDING = "PENDING"      # 대기 중
    PH1_OK = "PH1_OK"       # Phase 1 성공
    PH1_FAIL = "PH1_FAIL"   # Phase 1 실패 → Phase 2 스킵
    PH2_OK = "PH2_OK"       # Phase 2 성공
    PH2_FAIL = "PH2_FAIL"   # Phase 2 실패
    SKIPPED = "SKIPPED"     # 스킵됨


@dataclass
class FileAnalysisContext:
    """파일 분석 컨텍스트

    파일별 상태 추적으로 토큰 절감:
    - Phase1 실패 파일은 Phase2 LLM 호출을 스킵
    - 실패 사유를 기록하여 최종 리포트에 포함

    Attributes:
        directory: 파일이 위치한 디렉토리
        file_name: 파일명
        ast_data: 파싱된 AST JSON 데이터
        source_content: 원본 소스 파일 내용 (AST JSON에 code가 없을 때 fallback)
        processor: AST 프로세서 인스턴스 (Phase 1에서 생성, Phase 2에서 재사용)
        status: 현재 파일 상태
        error_message: 실패 시 에러 메시지
    """
    directory: str
    file_name: str
    ast_data: dict
    source_content: str = ""  # 소스 파일 원본 (startLine/endLine으로 코드 추출용)
    processor: Optional[Any] = None  # DbmsAstProcessor | FrameworkAstProcessor
    status: FileStatus = field(default=FileStatus.PENDING)
    error_message: str = ""


# ==================== ProcedureInfo ====================

class ProcedureInfo:
    """프로시저/함수 정보"""
    __slots__ = ('key', 'procedure_type', 'procedure_name', 'schema_name', 'start_line', 'end_line')

    def __init__(
        self,
        key: str,
        procedure_type: str,
        procedure_name: str,
        schema_name: Optional[str],
        start_line: int,
        end_line: int,
    ):
        self.key = key
        self.procedure_type = procedure_type
        self.procedure_name = procedure_name
        self.schema_name = schema_name
        self.start_line = start_line
        self.end_line = end_line


__all__ = [
    "StatementNode",
    "AnalysisBatch",
    "BatchPlanner",
    "FileStatus",
    "FileAnalysisContext",
    "ProcedureInfo",
]
