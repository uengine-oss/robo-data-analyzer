"""도구 호출 친화 메시지 카탈로그 — ``ToolEventEmitterMiddleware`` 가 사용.

도구 이름 → 한국어 친화 메시지 template (Python ``str.format`` 치환).
``{arg_name}`` placeholder 는 도구 호출 인자로 자동 치환.

새 도구 추가 시 손대는 곳: 이 dict 1곳만.
"""
from __future__ import annotations


# 도구 이름 → 친화 메시지 template
TOOL_MESSAGES: dict[str, str] = {
    # Profile Agent 도구 (step3_profile_project)
    "ls":                "{path} 디렉토리 보는 중...",
    "glob_search":       "{pattern} 파일 패턴 검색 중...",
    "grep":              "{pattern} 정규식 검색 중...",
    "read_file":         "{path} 파일 읽는 중...",
    "read_file_range":   "{path} 의 {start}~{end} 라인 정밀 읽는 중...",
    "list_functions":    "{file} 의 함수 목록 추출 중...",
    "list_skills":       "사용 가능한 SKILL 목록 조회 중...",
    "read_skill":        "{name} SKILL 본문 읽는 중...",
    "write_todos":       "할 일 목록 갱신 중...",

    # Linking Agent 도구 (step4_linking/stage3_resolve_unresolved)
    "bundle_search_lines":      "{query} 코드 라인 검색 중...",
    "ddl_lookup":               "DDL 테이블 {table_name} 조회 중...",
    "graph_get_callers":        "함수 {function_id} 의 caller 추적 중 (depth={depth})...",
    "graph_get_callees":        "함수 {function_id} 의 callee 추적 중 (depth={depth})...",
    "graph_create_table_access": "엣지 등록: {function_id} → {table} ({crud})",
    "graph_create_refer_to":    "REFER_TO 엣지 등록: {target_type}/{target_key}",
}
