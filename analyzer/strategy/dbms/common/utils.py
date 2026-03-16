"""DBMS 전략 공통 유틸리티 함수

phase_ddl.py + _ast_core.py에서 추출한 순수 유틸 함수.
DDL 관련 공통 함수는 analyzer.common.ddl_utils로 이동, 여기서 re-export.
"""

from __future__ import annotations

import re
from typing import Optional, Set, Tuple

# DDL 공통 함수 re-export (하위호환)
from analyzer.common.ddl_utils import apply_name_case, list_ddl_files  # noqa: F401


def resolve_default_schema(
    directory: str,
    ddl_schemas: Set[str],
    name_case: str = 'original'
) -> str:
    """파일 경로에서 기본 스키마를 결정합니다."""
    if not directory:
        return apply_name_case("public", name_case)

    parts = directory.replace("\\", "/").split("/")
    parts = [p for p in parts if p]

    if not parts:
        return apply_name_case("public", name_case)

    if ddl_schemas:
        ddl_schemas_lower_map = {s.lower(): s for s in ddl_schemas}
        for folder in reversed(parts):
            matched = ddl_schemas_lower_map.get(folder.lower())
            if matched:
                return matched

    return apply_name_case(parts[-1], name_case)


# ==================== _ast_core.py에서 추출 ====================

def get_procedure_name_from_code(code: str) -> Tuple[Optional[str], Optional[str]]:
    """코드 문자열에서 스키마/프로시저 이름을 추출합니다."""
    pattern = re.compile(
        r"\b(?:CREATE\s+(?:OR\s+REPLACE\s+)?)?(?:PROCEDURE|FUNCTION|TRIGGER)\s+"
        r"((?:\"[^\"]+\"|[A-Za-z_][\w$#]*)"
        r"(?:\s*\.\s*(?:\"[^\"]+\"|[A-Za-z_][\w$#]*)){0,2})",
        re.IGNORECASE,
    )
    prefix_pattern = re.compile(r"^\d+\s*:\s*")
    normalized = prefix_pattern.sub("", code)
    match = pattern.search(normalized)
    if not match:
        return None, None
    parts = [segment.strip().strip('"') for segment in re.split(r"\s*\.\s*", match.group(1))]
    if len(parts) == 3:
        return parts[0], f"{parts[1]}.{parts[2]}"
    if len(parts) == 2:
        return parts[0], parts[1]
    if parts:
        return None, parts[0]
    return None, None


def build_statement_name(node_type: str, start_line: int) -> str:
    """노드 타입과 시작 라인을 조합한 식별자 문자열을 생성합니다."""
    return f"{node_type}[{start_line}]"
