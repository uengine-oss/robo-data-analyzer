"""INIT_BY 관계 결정론적 매칭.

변수 초기화 시 다른 변수/상수의 값으로 초기화되는 패턴을 감지하여
(VARIABLE)-[:INIT_BY]->(VARIABLE|CONSTANT) 관계를 생성한다.

[탐지 패턴]
  C:      int limit = MAX_LOANS;
  Java:   private int max = Constants.MAX_VALUE;
  Python: limit = MAX_LOANS

[처리 흐름]
  1. variable_facts_list에서 code(소스코드)를 파싱
  2. 초기화 우변에서 식별자(변수/상수명) 추출
  3. 같은 모듈 또는 전체 범위에서 해당 이름의 variable_id 탐색
  4. 매칭되면 InitByEdge 생성
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional

from analyzer.strategy.framework.common.models import InitByEdge, VariableFacts

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 상수
# ---------------------------------------------------------------------------

# 초기화 우변에서 식별자를 추출하는 정규식
# 예: "int limit = MAX_LOANS;" → "MAX_LOANS"
# 예: "const char* msg = ERROR_MSG;" → "ERROR_MSG"
# 예: "#define"이나 "enum" 값은 제외 (자기 자신의 정의)
_INIT_PATTERN = re.compile(
    r'=\s*([A-Z][A-Z0-9_]{2,})\s*[;,)\]}]',  # ALL_CAPS 상수 패턴
)

# 점 접근 패턴: Constants.MAX_VALUE, Config.TIMEOUT 등
_DOT_INIT_PATTERN = re.compile(
    r'=\s*\w+\.([A-Z][A-Z0-9_]{2,})\s*[;,)\]}]',
)

# 단순 대입 패턴 (대문자가 아닌 변수도 포함)
_SIMPLE_INIT_PATTERN = re.compile(
    r'=\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*[;,)\]}]',
)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def build_init_by_edges(
    variable_facts_list: List[VariableFacts],
) -> List[InitByEdge]:
    """변수 초기화 관계를 결정론적으로 매칭한다.

    처리 흐름:
      1. 모든 변수/상수의 이름 → variable_id 인덱스 구축
      2. 각 변수의 code(소스코드)에서 초기화 우변 식별자 추출
      3. 인덱스에서 해당 이름의 variable_id 탐색
      4. 매칭되면 InitByEdge 생성 (자기 자신 제외)

    Args:
        variable_facts_list: VariableFacts 리스트 (Phase 1 AST 결과).

    Returns:
        InitByEdge 리스트.
    """
    if not variable_facts_list:
        return []

    # ── 1. 이름 → variable_id 인덱스 ──
    name_to_ids = _build_name_index(variable_facts_list)

    # ── 2~4. 각 변수의 초기화 출처 탐색 ──
    edges: List[InitByEdge] = []
    seen: set = set()

    for v in variable_facts_list:
        code = v.code or ""
        if not code:
            continue

        # 초기화 우변에서 식별자 추출
        source_names = _extract_init_source_names(code)
        if not source_names:
            continue

        for source_name in source_names:
            source_ids = name_to_ids.get(source_name, [])
            for source_id in source_ids:
                # 자기 자신 제외
                if source_id == v.variable_id:
                    continue
                # 중복 방지
                pair = (v.variable_id, source_id)
                if pair in seen:
                    continue
                seen.add(pair)
                edges.append(InitByEdge(
                    variable_id=v.variable_id,
                    init_source_id=source_id,
                ))

    logger.info("[INIT_BY] 결정론적 매칭 완료 | edges=%d (variables=%d)", len(edges), len(variable_facts_list))
    return edges


# ---------------------------------------------------------------------------
# 내부 함수
# ---------------------------------------------------------------------------


def _build_name_index(
    variable_facts_list: List[VariableFacts],
) -> Dict[str, List[str]]:
    """변수/상수 이름 → variable_id 리스트 인덱스를 구축한다.

    같은 이름이 여러 모듈에 존재할 수 있으므로 리스트로 관리.
    """
    index: Dict[str, List[str]] = {}
    for v in variable_facts_list:
        if v.name and v.variable_id:
            index.setdefault(v.name, []).append(v.variable_id)
    return index


def _extract_init_source_names(code: str) -> List[str]:
    """소스코드 문자열에서 초기화 우변의 식별자를 추출한다.

    우선순위:
      1. ALL_CAPS 상수 패턴 (예: MAX_LOANS, ERROR_CODE)
      2. 점 접근 상수 (예: Constants.MAX_VALUE → MAX_VALUE)

    Returns:
        추출된 식별자 이름 리스트 (중복 제거).
    """
    names: List[str] = []
    seen: set = set()

    # ALL_CAPS 상수 패턴
    for m in _INIT_PATTERN.finditer(code):
        name = m.group(1)
        if name not in seen and not _is_keyword(name):
            seen.add(name)
            names.append(name)

    # 점 접근 패턴
    for m in _DOT_INIT_PATTERN.finditer(code):
        name = m.group(1)
        if name not in seen and not _is_keyword(name):
            seen.add(name)
            names.append(name)

    return names


def _is_keyword(name: str) -> bool:
    """C/Java/Python 키워드인지 확인한다."""
    return name in _KEYWORDS


_KEYWORDS = frozenset({
    # C
    "NULL", "TRUE", "FALSE", "EOF", "CHAR", "INT", "LONG", "FLOAT", "DOUBLE",
    "VOID", "CONST", "STATIC", "EXTERN", "SIZEOF", "RETURN",
    # Java
    "THIS", "SUPER", "CLASS", "INTERFACE", "ENUM",
    # Python
    "NONE", "SELF",
    # 공통 타입
    "STRING", "BOOLEAN", "INTEGER",
})
