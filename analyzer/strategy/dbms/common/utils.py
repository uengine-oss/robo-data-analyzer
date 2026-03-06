"""DBMS 전략 공통 유틸리티 함수

phase_ddl.py + _ast_core.py에서 추출한 순수 유틸 함수.
"""

from __future__ import annotations

import os
import re
from typing import Any, List, Optional, Set, Tuple

from util.text_utils import log_process


# ==================== phase_ddl.py에서 추출 ====================

def apply_name_case(name: str, name_case: str) -> str:
    """메타데이터 대소문자 변환 적용"""
    if not name:
        return name
    if name_case == "uppercase":
        return name.upper()
    elif name_case == "lowercase":
        return name.lower()
    return name


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


def list_ddl_files(orchestrator: Any) -> List[str]:
    """DDL 파일 목록 조회

    DDL 디렉토리가 없거나 파일이 없으면 빈 리스트 반환 (경고 처리, 에러 아님)
    """
    ddl_dir = orchestrator.dirs.get("ddl", "")
    if not ddl_dir:
        log_process("ANALYZE", "DDL", "DDL 디렉토리 설정 없음 - DDL 처리 생략")
        return []
    if not os.path.isdir(ddl_dir):
        log_process("ANALYZE", "DDL", f"DDL 디렉토리 없음: {ddl_dir} - DDL 처리 생략")
        return []
    try:
        files = sorted(
            f for f in os.listdir(ddl_dir)
            if os.path.isfile(os.path.join(ddl_dir, f))
        )
        if not files:
            log_process("ANALYZE", "DDL", f"DDL 디렉토리에 파일 없음: {ddl_dir} - DDL 처리 생략")
            return []
        log_process("ANALYZE", "DDL", f"DDL 파일 발견: {len(files)}개")
        return files
    except OSError as e:
        log_process("ANALYZE", "DDL", f"DDL 디렉토리 읽기 실패: {ddl_dir} - {e}")
        return []


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
