"""DDL 테이블 이름 결정론 매칭 + LLM context 빌딩.

DBMS Phase 2B (dml_extract LLM) 호출 직전, range 의 code_slice 에서 DDL 적재된
테이블 이름이 단어로 등장하는지 정규식 단어 매칭으로 결정론적 식별.

매칭된 테이블의 DDL 메타 (description + 컬럼 목록) 를 LLM 프롬프트의 ddl_context
슬롯에 주입 → LLM 이 메타 재추출 X (DDL 신뢰), 환각 차단, 토큰 절감.

흐름:
  Phase 0 끝 : build_pattern(table_names) → re.Pattern (1회만)
  Phase 2B per range : match_in_code + build_context → LLM 입력 ddl_context

알고리즘: single regex alternation + word boundary (`\\b`) + IGNORECASE.
- Python re engine 이 alternation 을 효율 자동기로 컴파일 → 1000~10000 테이블까지 충분.
- 더 큰 규모는 향후 pyahocorasick 옵션으로 교체 가능 (인터페이스 동일).
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Set


def build_pattern(table_names: List[str]) -> Optional[re.Pattern]:
    """DDL 테이블 이름 list → 단일 컴파일된 정규식.

    Phase 0 (DDL 적재) 끝에 1회만 호출. 결과 패턴은 ctx 에 보관해 Phase 2B 에서 재사용.

    Args:
        table_names: DDL 에 정의된 모든 테이블 이름 리스트 (대소문자 무관).

    Returns:
        컴파일된 정규식 패턴, 또는 빈 입력 시 None.

    Notes:
        - `\\b` 단어 경계로 부분 문자열 매칭 차단 (예: MY_RDITAG_TB_VAR 안 매칭).
        - re.IGNORECASE 로 대소문자 차이 흡수.
        - re.escape 로 메타 문자 안전 처리.
    """
    if not table_names:
        return None
    escaped = "|".join(re.escape(n) for n in table_names)
    return re.compile(r'\b(?:' + escaped + r')\b', re.IGNORECASE)


def match_in_code(code_slice: str, pattern: Optional[re.Pattern]) -> Set[str]:
    """code_slice 에서 매칭된 DDL 테이블 이름 set (대문자 정규화).

    [하위호환] 위치 정보 없이 set 만 필요한 경우용. 신규 사용은
    ``match_in_code_with_lines`` 를 권장 (라인 매핑 포함).
    """
    if not pattern or not code_slice:
        return set()
    return {m.upper() for m in pattern.findall(code_slice)}


def match_in_code_with_lines(
    code_with_line_prefix: str,
    pattern: Optional[re.Pattern],
) -> Dict[str, List[int]]:
    """code 에서 매칭된 DDL 테이블 → 등장 라인 번호 리스트 dict.

    code 는 ast_collector 가 만든 ``"<line>: <text>"`` 형식이라 가정.
    각 라인을 분리해 매칭하고, 테이블별로 등장한 라인 번호를 수집.

    Args:
        code_with_line_prefix: ``"<line>: <text>"`` 행들이 ``\\n`` 으로 구분된 텍스트
        pattern: build_pattern 결과. None 이면 빈 dict.

    Returns:
        {대문자 테이블명: [라인 번호 정렬 리스트]}.
        매칭 0 이면 빈 dict.
    """
    if not pattern or not code_with_line_prefix:
        return {}
    line_re = re.compile(r"^(\d+):\s?(.*)$")
    result: Dict[str, Set[int]] = {}
    for raw_line in code_with_line_prefix.split("\n"):
        m = line_re.match(raw_line)
        if not m:
            continue
        line_no = int(m.group(1))
        text = m.group(2)
        for hit in pattern.findall(text):
            result.setdefault(hit.upper(), set()).add(line_no)
    return {name: sorted(lines) for name, lines in result.items()}


def format_line_ranges(lines: List[int]) -> str:
    """라인 번호 list → 사람이 읽는 범위 표현. 연속 구간은 ``s-e`` 로 압축.

    예: [3, 4, 5, 10, 12, 13] → "3-5, 10, 12-13"
    """
    if not lines:
        return ""
    sorted_lines = sorted(set(lines))
    parts: List[str] = []
    start = prev = sorted_lines[0]
    for n in sorted_lines[1:]:
        if n == prev + 1:
            prev = n
            continue
        parts.append(f"{start}-{prev}" if start != prev else f"{start}")
        start = prev = n
    parts.append(f"{start}-{prev}" if start != prev else f"{start}")
    return ", ".join(parts)


def build_context(
    matched: Set[str] | Dict[str, List[int]],
    ddl_table_index: Dict[str, dict],
) -> str:
    """매칭 + DDL 인덱스 → LLM 프롬프트 ddl_context 문자열.

    형식 (라인 매핑 포함):
        - <TABLE> (사용 라인: <ranges>): <table description>
          컬럼: <col>(<dtype>, NOT NULL, <desc>), ...

    Args:
        matched: ``match_in_code`` (set) 또는 ``match_in_code_with_lines`` (dict) 결과.
            dict 면 라인 매핑이 함께 출력됨.
        ddl_table_index: Phase 0 의 DDL 인덱스.

    Returns:
        ddl_context 슬롯에 주입 가능한 문자열. 매칭 0 이면 빈 문자열.
    """
    if not matched:
        return ""
    is_with_lines = isinstance(matched, dict)
    names = sorted(matched.keys()) if is_with_lines else sorted(matched)
    lines: List[str] = []
    for name in names:
        info = ddl_table_index.get(name)
        if not info:
            continue
        col_parts: List[str] = []
        for c in info.get('columns', []):
            attrs: List[str] = []
            dtype = (c.get('dtype') or '').strip()
            if dtype:
                attrs.append(dtype)
            if c.get('nullable') is False:
                attrs.append("NOT NULL")
            desc = (c.get('description') or '').strip()
            if desc:
                attrs.append(desc)
            col_parts.append(f"{c['name']}({', '.join(attrs)})")
        cols = ", ".join(col_parts)
        table_desc = info.get('description', '') or ''
        if is_with_lines:
            line_str = format_line_ranges(matched.get(name) or [])
            header = f"- {name} (사용 라인: {line_str}): {table_desc}" if line_str else f"- {name}: {table_desc}"
            lines.append(f"{header}\n  컬럼: {cols}")
        else:
            lines.append(f"- {name}: {table_desc} | 컬럼: {cols}")
    return "\n".join(lines)


__all__ = [
    "build_pattern",
    "match_in_code",
    "match_in_code_with_lines",
    "build_context",
    "format_line_ranges",
]
