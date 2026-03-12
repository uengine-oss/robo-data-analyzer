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

# Entity prefixes
ENTITY_PREFIX = "entity::"
ENTITY_XML_PREFIX = "entity_xml::"
EXTERNAL_PREFIX = "external:"
EXTERNAL_SEP = "#"

# Log prefixes
LOG_SCAN = "[FW:SCAN]"
LOG_AST = "[FW:AST]"
LOG_LINK = "[FW:LINK]"
LOG_ANALYSIS = "[FW:ANALYSIS]"
LOG_SUMMARY = "[FW:SUMMARY]"
LOG_GRAPH = "[FW:GRAPH]"

# Entity annotation signals
ENTITY_SIGNALS_ANNOTATION = ["@Entity", "@Table", "@MappedSuperclass", "@Document", "@RedisHash"]

# Scan skip directories
SKIP_DIRS = frozenset({"ddl", ".git", "node_modules", "__pycache__", ".venv", "venv", "target", "build"})

# File categories
CATEGORY_JAVA_AST = "java_ast"
CATEGORY_XML_SQLMAP = "xml_sqlmap"
CATEGORY_XML_ARTIFACT = "xml_artifact"
CATEGORY_XML_EJB = CATEGORY_XML_ARTIFACT  # 하위호환 별칭
CATEGORY_XML_CMP_MAPPING = "xml_cmp_mapping"
CATEGORY_XML = "xml"
CATEGORY_YAML = "yaml"
CATEGORY_PROPERTIES = "properties"
CATEGORY_JSON = "json"
CATEGORY_DOCS = "docs"
