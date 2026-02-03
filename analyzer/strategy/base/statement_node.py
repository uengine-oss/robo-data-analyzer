"""공통 StatementNode 데이터 클래스

DBMS와 Framework 전략에서 공유하는 AST 노드 표현.

주요 특징:
- unit_key/unit_name/unit_kind로 프로시저/클래스 정보 통합
- 공통 메서드: get_raw_code, get_compact_code, get_skeleton_code, get_ancestor_context
- completion_event/context_ready_event로 비동기 의존성 관리
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import List, Optional, Tuple, Set

from config.settings import settings
from util.text_utils import calculate_code_token, log_process

# 설정에서 가져오는 상수
MAX_CONTEXT_TOKEN = settings.batch.max_context_token


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
    
    # AST JSON에서 추출한 메타데이터 (선택적)
    signature: Optional[str] = None        # 함수/프로시저 시그니처
    modifiers: Optional[str] = None        # public, private, static 등 수정자
    return_type: Optional[str] = None      # 반환 타입
    parameters: Optional[str] = None       # 파라미터 문자열
    generic_type: Optional[str] = None     # 제네릭 타입
    extends_type: Optional[str] = None     # 상속 타입 (extends)
    implements_types: Optional[str] = None # 구현 인터페이스 (implements)
    field_type: Optional[str] = None       # 필드 타입
    
    # Framework 전용: 어노테이션 정보
    annotations: Optional[List[str]] = None  # ["@Entity", "@Table(name='orders')"]
    
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
    
    # 비동기 이벤트
    completion_event: asyncio.Event = field(init=False, repr=False)
    context_ready_event: asyncio.Event = field(init=False, repr=False)

    def __post_init__(self):
        object.__setattr__(self, "completion_event", asyncio.Event())
        object.__setattr__(self, "context_ready_event", asyncio.Event())

    def get_raw_code(self) -> str:
        """라인 번호를 포함하여 노드의 원문 코드를 반환합니다."""
        return '\n'.join(f"{line_no}: {text}" for line_no, text in self.lines)

    def get_compact_code(self) -> str:
        """자식 요약을 포함한 부모 코드(LLM 입력용)를 생성합니다.
        
        DBMS와 Framework 공통 로직:
        - 자식 이전의 부모 고유 코드를 그대로 복사
        - 자식 구간은 자식 요약으로 대체 (없으면 원문 보관)
        - 마지막 자식 이후 부모 코드 추가
        """
        if not self.children:
            return self.code  # 원본과 동일하게 self.code 반환

        result_lines: List[str] = []
        line_index = 0
        total_lines = len(self.lines)
        sorted_children = sorted(self.children, key=lambda child: child.start_line)

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
                log_process("ANALYZE", "COLLECT", f"⚠️ 부모 {self.start_line}~{self.end_line}의 자식 {child.start_line}~{child.end_line} (type={child.node_type}, analyzable={child.analyzable}) 요약 없음 - 원문 보관")
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

        result_lines: List[str] = []
        line_index = 0
        total_lines = len(self.lines)
        sorted_children = sorted(self.children, key=lambda child: child.start_line)

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

        result_lines: List[str] = []
        sorted_children = sorted(self.children, key=lambda child: child.start_line)
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

