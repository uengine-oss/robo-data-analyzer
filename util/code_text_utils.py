"""ROBO Analyzer 유틸리티 모듈

핵심 유틸리티 함수:
- 토큰 계산
- 문자열 처리 (라인 번호, 슬라이스)
- 파라미터 파싱
"""

import json
import logging
import tiktoken
from typing import Dict, List, Union


# tiktoken 인코더 초기화
ENCODER = tiktoken.get_encoding("cl100k_base")


#==============================================================================
# 문자열/JSON 유틸리티
#==============================================================================

def add_line_numbers(content: str) -> str:
    """원본 소스 내용에 각 줄 앞에 'N: '를 붙인 문자열 반환.

    경로/파일 읽기는 하지 않음. 호출측에서 읽은 content만 넘김.
    """
    if not content:
        return ""
    lines = content.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    return "\n".join(f"{i + 1}: {line}" for i, line in enumerate(lines))


def slice_by_line_range(
    text_with_line_numbers: str,
    start_line: int,
    end_line: int,
) -> str:
    """라인번호가 붙은 텍스트에서 start_line~end_line 구간만 추출.

    add_line_numbers() 출력 또는 동일 형태(각 줄 "N: 내용")를 가정.
    """
    if start_line < 1 or end_line < start_line:
        return ""
    lines = text_with_line_numbers.split("\n")
    start_idx = start_line - 1
    end_idx = min(end_line, len(lines))
    if start_idx >= len(lines):
        return ""
    return "\n".join(lines[start_idx:end_idx])


def resolve_node_code(node: dict, source_content: str) -> str:
    """AST 노드에 해당하는 코드 문자열 반환.

    JSON에는 code 필드가 없으므로, 항상 원본(source_content)을 노드의
    startLine/endLine으로 슬라이스한 결과만 반환한다.
    """
    if not source_content:
        return ""
    start_line = node.get("startLine", 0) or node.get("start_line", 0)
    end_line = node.get("endLine", 0) or node.get("end_line", start_line)
    if start_line < 1 or end_line < start_line:
        return ""
    text_with_lines = add_line_numbers(source_content)
    return slice_by_line_range(text_with_lines, start_line, end_line)


#==============================================================================
# 스키마/테이블 파싱 유틸리티
#==============================================================================

def parse_table_identifier(qualified_table_name: str) -> tuple[str, str, str | None]:
    """'SCHEMA.TABLE@DBLINK'에서 (schema, table, dblink) 추출

    따옴표(", ', `, [])를 자동으로 제거합니다.
    원본 대소문자를 유지합니다 (name_case 옵션에서 변환 처리).
    예: "RWIS"."TABLE" → (RWIS, TABLE, None)
    """
    if not qualified_table_name:
        return '', '', None

    def strip_quotes(s: str) -> str:
        """따옴표 제거: "name", 'name', `name`, [name] 형식 처리"""
        s = s.strip()
        if len(s) >= 2:
            if (s[0] == '"' and s[-1] == '"') or \
               (s[0] == "'" and s[-1] == "'") or \
               (s[0] == '`' and s[-1] == '`'):
                return s[1:-1]
            if s[0] == '[' and s[-1] == ']':
                return s[1:-1]
        return s

    text = qualified_table_name.strip()
    left, _, link = text.partition('@')
    s, _, t = left.partition('.')

    schema_raw = strip_quotes(s.strip()) if t else ''
    table_raw = strip_quotes(t.strip()) if t else strip_quotes(left.strip())
    link_raw = strip_quotes(link.strip()) if link.strip() else None

    # 원본 대소문자 유지 (name_case 옵션에서 변환 처리)
    schema = schema_raw or ''
    table = table_raw or ''
    db_link = link_raw if link_raw else None

    return schema, table, db_link


#==============================================================================
# 코드 분석 유틸리티
#==============================================================================

def calculate_code_token(code: Union[str, Dict, List]) -> int:
    """코드 토큰 길이 계산"""
    try:
        if isinstance(code, str):
            text = code
        else:
            text = json.dumps(code, ensure_ascii=False)
        return len(ENCODER.encode(text))
    except Exception as e:
        err_msg = f"토큰 계산 도중 문제가 발생: {str(e)}"
        logging.error(err_msg)
        raise RuntimeError(err_msg)


# ---------------------------------------------------------------------------
# 파라미터 파싱 (범용)
# ---------------------------------------------------------------------------

def _parse_type_name(params_str: str) -> Dict[str, str]:
    """'Type name' 형식 (Java, C): 'int count, String name' → {count: int, name: String}."""
    param_map: Dict[str, str] = {}
    if not params_str or not isinstance(params_str, str):
        return param_map
    raw = params_str.strip()
    if not raw:
        return param_map
    for part in raw.split(","):
        seg = part.strip().replace("(", " ").replace(")", " ")
        tokens = [t for t in seg.split() if t]
        if len(tokens) < 2:
            continue
        param_name = tokens[-1]
        param_type = tokens[-2]
        if param_name not in param_map:
            param_map[param_name] = param_type
    return param_map


def _parse_name_colon_type(params_str: str) -> Dict[str, str]:
    """'name: Type' 형식 (Python, Kotlin): 'name: str, age: int' → {name: str, age: int}."""
    param_map: Dict[str, str] = {}
    if not params_str or not isinstance(params_str, str):
        return param_map
    raw = params_str.strip()
    if not raw:
        return param_map
    for part in raw.split(","):
        if ":" not in part:
            continue
        name_part, type_part = part.split(":", 1)
        name = name_part.strip()
        typ = type_part.strip()
        if name and typ and name not in param_map:
            param_map[name] = typ
    return param_map


def _parse_name_type(params_str: str) -> Dict[str, str]:
    """'name Type' 형식 (Go): 'count int, name string' → {count: int, name: string}."""
    param_map: Dict[str, str] = {}
    if not params_str or not isinstance(params_str, str):
        return param_map
    raw = params_str.strip()
    if not raw:
        return param_map
    for part in raw.split(","):
        seg = part.strip()
        tokens = [t for t in seg.split() if t]
        if len(tokens) < 2:
            continue
        param_name = tokens[0]
        param_type = tokens[1]
        if param_name not in param_map:
            param_map[param_name] = param_type
    return param_map


_PARAM_PARSERS = {
    "type_name": _parse_type_name,
    "name_colon_type": _parse_name_colon_type,
    "name_type": _parse_name_type,
}


def parse_params(params_str: str, style: str = "type_name") -> Dict[str, str]:
    """범용 파라미터 파서. style에 따라 적절한 파서 선택."""
    parser = _PARAM_PARSERS.get(style, _parse_type_name)
    return parser(params_str)


