"""Framework 공통 상수."""

# Table.db / Table.schema 의 임의 default 는 박지 않는다.
# 사용처는 settings.defaults.db_dialect / settings.defaults.db_schema 를 직접 참조.
# env (DEFAULT_DB_DIALECT / DEFAULT_DB_SCHEMA) 또는 호출자가 명시하지 않으면 빈 문자열 ("").
# "postgres"/"public" 같은 임의값을 박으면 Oracle/DB2 등 다른 dialect 시스템에서 mismatch 가
# 발생하므로 모르면 빈 채로 두는 정책.

# Excluded tables (Oracle dual 등)
EXCLUDED_TABLE_NAMES = frozenset({"dual", "sysibm.sysdummy1"})

# Code/text truncation limits
CODE_TEXT_MAX = 8000
SQL_TEXT_MAX = 8000
SQL_LLM_CHUNK_MAX = 7000
SQL_SPLIT_THRESHOLD = 8000
SQL_MAX_CHUNKS = 20
ARTIFACT_CODE_MAX = 8000

# Scan skip directories
SKIP_DIRS = frozenset({"ddl", ".git", "node_modules", "__pycache__", ".venv", "venv", "target", "build"})

# File categories — 범용 (용도 기반)
CATEGORY_SOURCE_AST = "source_ast"       # 소스코드 AST (모든 언어)
CATEGORY_SQL = "sql"                     # SQL 매핑
CATEGORY_ENTITY = "entity"               # 엔티티 정의
CATEGORY_CONFIG = "config"               # 설정 파일 (yaml, properties, json, 일반 xml)
CATEGORY_DOCS = "docs"                   # 문서
