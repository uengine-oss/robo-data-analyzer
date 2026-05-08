"""Framework 공통 상수."""

# Table.db (dialect) default: settings.defaults.db_dialect ('postgres') 사용 (env 로 override).
# Table.schema 결정: analyzer.common.cypher.schema_resolver.resolve_schema 위계 적용
#   (LLM 명시 → DDL 매칭 → own_schema → 빈값). 강제 'public' 박지 X.

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

# 노드 labels 에서 가장 구체적인 변수성 라벨 1개 추출할 때 우선순위.
# code_analysis LLM 입력 `referenced_symbols` 의 [TYPE] 라벨 결정에 사용.
SYMBOL_LABEL_PRIORITY = (
    "DEFINE", "CONSTANT_FIELD", "CONSTANT",
    "GLOBAL_VARIABLE", "FIELD", "MEMBER", "PARAMETER", "VARIABLE",
)
