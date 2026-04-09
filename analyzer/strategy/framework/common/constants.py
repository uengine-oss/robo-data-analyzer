"""Framework 공통 상수."""

# Table defaults (DBMS pipeline 호환)
TABLE_DB_DEFAULT = "postgres"
TABLE_SCHEMA_DEFAULT = "public"

# Excluded tables (Oracle dual 등)
EXCLUDED_TABLE_NAMES = frozenset({"dual", "sysibm.sysdummy1"})

# Code/text truncation limits
CODE_TEXT_MAX = 8000
SQL_TEXT_MAX = 8000
SQL_LLM_CHUNK_MAX = 7000
SQL_SPLIT_THRESHOLD = 8000
SQL_MAX_CHUNKS = 20
ARTIFACT_CODE_MAX = 8000

EXTERNAL_PREFIX = "external:"
EXTERNAL_SEP = "#"

# Log prefixes
LOG_SCAN = "[PHASE:1:SCAN]"
LOG_AST = "[PHASE:1:AST]"
LOG_LINK = "[PHASE:2:LINKING]"
LOG_ANALYSIS = "[PHASE:3:CODE_ANALYSIS]"
LOG_SUMMARY = "[PHASE:3:CODE_ANALYSIS]"
LOG_GRAPH = "[GRAPH]"

# Scan skip directories
SKIP_DIRS = frozenset({"ddl", ".git", "node_modules", "__pycache__", ".venv", "venv", "target", "build"})

# File categories — 범용 (용도 기반)
CATEGORY_SOURCE_AST = "source_ast"       # 소스코드 AST (모든 언어)
CATEGORY_SQL = "sql"                     # SQL 매핑
CATEGORY_ENTITY = "entity"               # 엔티티 정의
CATEGORY_CONFIG = "config"               # 설정 파일 (yaml, properties, json, 일반 xml)
CATEGORY_DOCS = "docs"                   # 문서
