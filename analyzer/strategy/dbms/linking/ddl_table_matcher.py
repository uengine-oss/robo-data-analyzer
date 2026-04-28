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

    Phase 2B 의 dml_extract 호출 직전 per range 호출. 매칭 자체는 Python re 의
    단일 패스 — 패턴 size 와 무관하게 빠름 (< 1ms / 1KB).

    Args:
        code_slice: range 의 SQL 본문 (startLine~endLine).
        pattern: build_pattern 결과. None 이면 빈 set 반환.

    Returns:
        매칭된 테이블 이름의 대문자 정규화 set.
    """
    if not pattern or not code_slice:
        return set()
    return {m.upper() for m in pattern.findall(code_slice)}


def build_context(matched: Set[str], ddl_table_index: Dict[str, dict]) -> str:
    """매칭 set + DDL 인덱스 → LLM 프롬프트 ddl_context 문자열.

    형식:
        - <TABLE>: <table description> | 컬럼: <col>(<dtype>, NOT NULL, <desc>), ...

    dtype / NOT NULL 은 LLM 의 example.inputs 정확도와 경계값 생성에 필수
    (NUMBER vs VARCHAR2 리터럴 형식, NULL 허용 여부에 따른 경계 케이스).
    빈 속성은 자동 생략 — `COL()` 형식 가능.

    Args:
        matched: match_in_code 결과 set.
        ddl_table_index: Phase 0 의 DDL 인덱스 dict.

    Returns:
        LLM 프롬프트의 ddl_context 슬롯에 그대로 주입 가능한 문자열.
        매칭 0 이면 빈 문자열.
    """
    if not matched:
        return ""
    lines: List[str] = []
    for name in sorted(matched):
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
        lines.append(f"- {name}: {table_desc} | 컬럼: {cols}")
    return "\n".join(lines)


__all__ = ["build_pattern", "match_in_code", "build_context"]
