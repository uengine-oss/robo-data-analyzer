"""DBMS 전략 상수 정의

_ast_core.py에서 추출한 노드 타입 분류, 관계 매핑, 설정 상수.
"""

from config.settings import settings

# 노드 타입 분류
PROCEDURE_TYPES = frozenset(["PROCEDURE", "FUNCTION", "CREATE_PROCEDURE_BODY", "TRIGGER", "BEGIN"])
# LLM 분석 X — 구조 래퍼 / 단순 declaration. PARAMETER 는 함수 파라미터 (자식 노드, declaration 성격)
NON_ANALYSIS_TYPES = frozenset([
    "CREATE_PROCEDURE_BODY", "FILE", "PROCEDURE", "FUNCTION", "DECLARE", "TRIGGER", "SPEC",
    "VARIABLE", "PACKAGE_VARIABLE", "CONSTANT_FIELD", "PARAMETER",
])
DML_STATEMENT_TYPES = frozenset(["SELECT", "INSERT", "UPDATE", "DELETE", "MERGE", "EXECUTE_IMMEDIATE", "FETCH", "CREATE_TEMP_TABLE", "CTE", "OPEN_CURSOR", "CURSOR_VARIABLE"])
# 변수성 declaration — REFERENCES / INIT_BY 매칭의 target 후보. PARAMETER 도 포함 (함수 파라미터 = 변수성 식별자)
VARIABLE_DECLARATION_TYPES = frozenset(["PACKAGE_VARIABLE", "DECLARE", "SPEC", "PARAMETER"])

# Bottom-up Rule 누적에서 제외되는 구조 래퍼 — 자식 rules 를 그대로 통과
# 나머지 모든 노드는 rule_target (의미 부모 or 리프) 로 간주됨
STRUCTURAL_NODE_TYPES = frozenset([
    "PROCEDURE", "FUNCTION", "TRIGGER",
    "CREATE_PROCEDURE_BODY",
    "PACKAGE_BODY", "SPEC",
    "BEGIN",
    "FILE",
])

# DBMS 시스템 패키지 (CALL 관계 생성 제외 대상)
# Oracle/PostgreSQL 내장 패키지 — 프로시저 호출 시 이 패키지는 무시
DBMS_SYSTEM_PACKAGES = frozenset([
    # Oracle DBMS_* 패키지
    "DBMS_OUTPUT", "DBMS_UTILITY", "DBMS_LOB", "DBMS_SQL", "DBMS_LOCK",
    "DBMS_SESSION", "DBMS_METADATA", "DBMS_STATS", "DBMS_SCHEDULER",
    "DBMS_JOB", "DBMS_AQ", "DBMS_AQADM", "DBMS_PIPE", "DBMS_ALERT",
    "DBMS_RANDOM", "DBMS_CRYPTO", "DBMS_ROWID", "DBMS_SPACE",
    "DBMS_XMLGEN", "DBMS_XMLDOM", "DBMS_XMLPARSER", "DBMS_XSLPROCESSOR",
    "DBMS_PROFILER", "DBMS_TRACE", "DBMS_APPLICATION_INFO",
    "DBMS_TRANSACTION", "DBMS_FLASHBACK", "DBMS_REDEFINITION",
    "DBMS_REPAIR", "DBMS_RESUMABLE", "DBMS_RLS", "DBMS_FGA",
    # Oracle UTL_* 패키지
    "UTL_FILE", "UTL_HTTP", "UTL_SMTP", "UTL_TCP", "UTL_URL",
    "UTL_RAW", "UTL_ENCODE", "UTL_I18N", "UTL_COMPRESS", "UTL_MATCH",
    "UTL_REF", "UTL_INADDR", "UTL_CALL_STACK",
    # Oracle 기타 시스템 패키지
    "APEX_UTIL", "APEX_MAIL", "APEX_COLLECTION", "APEX_JSON",
    "HTP", "HTF", "OWA_UTIL",
    # PostgreSQL 시스템 함수 (스키마 접두사 형태)
    "PG_CATALOG", "PG_STAT", "PG_ADVISORY",
])

# 관계 매핑
TABLE_RELATIONSHIP_MAP = {"r": "FROM", "w": "WRITES"}
# 설정에서 가져오는 상수
MAX_BATCH_TOKEN = settings.batch.max_batch_token
MAX_CONCURRENCY = settings.concurrency.max_concurrency
MAX_SUMMARY_CHUNK_TOKEN = settings.batch.max_summary_chunk_token
MAX_CONTEXT_TOKEN = settings.batch.max_context_token
PARENT_EXPAND_THRESHOLD = settings.batch.parent_expand_threshold
