"""DBMS 전략 상수 정의

_ast_core.py에서 추출한 노드 타입 분류, 관계 매핑, 설정 상수.
"""

from config.settings import settings

# 노드 타입 분류
PROCEDURE_TYPES = frozenset(["PROCEDURE", "FUNCTION", "CREATE_PROCEDURE_BODY", "TRIGGER", "BEGIN"])
NON_ANALYSIS_TYPES = frozenset(["CREATE_PROCEDURE_BODY", "FILE", "PROCEDURE", "FUNCTION", "DECLARE", "TRIGGER", "SPEC"])
DML_STATEMENT_TYPES = frozenset(["SELECT", "INSERT", "UPDATE", "DELETE", "MERGE", "EXECUTE_IMMEDIATE", "FETCH", "CREATE_TEMP_TABLE", "CTE", "OPEN_CURSOR", "CURSOR_VARIABLE"])
VARIABLE_DECLARATION_TYPES = frozenset(["PACKAGE_VARIABLE", "DECLARE", "SPEC"])

# 관계 매핑
TABLE_RELATIONSHIP_MAP = {"r": "FROM", "w": "WRITES"}
VARIABLE_ROLE_MAP = {
    "PACKAGE_VARIABLE": "패키지 전역 변수",
    "DECLARE": "변수 선언및 초기화",
    "SPEC": "함수 및 프로시저 입력 매개변수",
}

# 설정에서 가져오는 상수
VARIABLE_CONCURRENCY = settings.concurrency.variable_concurrency
MAX_BATCH_TOKEN = settings.batch.max_batch_token
MAX_CONCURRENCY = settings.concurrency.max_concurrency
MAX_SUMMARY_CHUNK_TOKEN = settings.batch.max_summary_chunk_token
MAX_CONTEXT_TOKEN = settings.batch.max_context_token
PARENT_EXPAND_THRESHOLD = settings.batch.parent_expand_threshold
