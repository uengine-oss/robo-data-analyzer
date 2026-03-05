"""exp 데이터 모델: FileCatalog, ProcessingPlan, MethodFacts, SqlStatementChunk, 엣지 등."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# Step 1
# ---------------------------------------------------------------------------


@dataclass
class FileCatalogEntry:
    """파일 카탈로그 항목 (스캔 결과)."""
    path: str
    ext: str
    size: int
    encoding: str
    category: str  # java_ast | xml_sqlmap | yaml | properties | etc
    hash_val: Optional[str] = None


@dataclass
class ProcessingPlan:
    """처리 계획: 분류된 작업 목록."""
    java_ast_jobs: List[str] = field(default_factory=list)   # 파일 경로
    xml_sqlmap_jobs: List[str] = field(default_factory=list)
    xml_ejb_jobs: List[str] = field(default_factory=list)   # ejb-jar.xml
    xml_entitymodel_jobs: List[str] = field(default_factory=list)  # entitymodel.xml (ofbiz)
    yaml_jobs: List[str] = field(default_factory=list)
    properties_jobs: List[str] = field(default_factory=list)
    json_jobs: List[str] = field(default_factory=list)
    docs_jobs: List[str] = field(default_factory=list)  # 비정형문서 전용


# ---------------------------------------------------------------------------
# Step 2
# ---------------------------------------------------------------------------


@dataclass
class FieldFacts:
    """필드 단위 팩트 (Java AST에서 추출)."""
    field_id: str
    class_key: str
    name: str
    start_line: int
    end_line: int
    field_type: Optional[str] = None
    code_ref: Optional[str] = None
    comment: Optional[str] = None
    annotations: Optional[str] = None


@dataclass
class MethodFacts:
    """메서드 단위 팩트 (Java AST에서 추출)."""
    method_id: str
    fqn: str
    signature: str
    start_line: int
    end_line: int
    annotations: Optional[str] = None
    comment: Optional[str] = None
    return_type: Optional[str] = None
    params: Optional[str] = None
    code_ref: Optional[str] = None   # file_path + line_range 참조
    class_key: Optional[str] = None
    package_name: Optional[str] = None
    code: str = ""


# ---------------------------------------------------------------------------
# ChunkBundle (링킹/분석용, analysis JSON 있는 파일만)
# ---------------------------------------------------------------------------


@dataclass
class ChunkBundleFile:
    """ChunkBundle 메타: FILE 단위."""
    file_path: str
    file_hash: Optional[str] = None
    encoding: Optional[str] = None


@dataclass
class ChunkBundleMethod:
    """ChunkBundle 내 METHOD: method_id, span, (선택) method_code(slice+라인번호)."""
    method_id: str
    start_line: int
    end_line: int
    method_code: Optional[str] = None


@dataclass
class ChunkBundleMethodCall:
    """ChunkBundle 내 METHOD_CALL: parent_method_id, call_id, span, call_code(필수)."""
    parent_method_id: str
    call_id: str
    start_line: int
    end_line: int
    call_code: str  # slice로 생성, 라인번호 포함


@dataclass
class ChunkBundleVariable:
    """ChunkBundle 내 VARIABLE: parent_method_id, varName, varType, span, flags, initializer_code."""
    parent_method_id: str
    var_name: str
    var_type: Optional[str] = None
    start_line: int = 0
    end_line: int = 0
    flags: Optional[Dict[str, Any]] = None  # e.g. initializerContainsMethodCall
    initializer_code: Optional[str] = None  # slice when flags.initializerContainsMethodCall


@dataclass
class ChunkBundleField:
    """ChunkBundle 내 FIELD: field_id, span, field_code(선택)."""
    field_id: str
    start_line: int
    end_line: int
    field_code: Optional[str] = None


@dataclass
class ChunkBundle:
    """파일 단위 ChunkBundle: 메타 + METHOD/METHOD_CALL/VARIABLE/FIELD 목록."""
    file_meta: ChunkBundleFile
    methods: List[ChunkBundleMethod] = field(default_factory=list)
    method_calls: List[ChunkBundleMethodCall] = field(default_factory=list)
    variables: List[ChunkBundleVariable] = field(default_factory=list)
    fields: List[ChunkBundleField] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Step 3
# ---------------------------------------------------------------------------


@dataclass
class SqlStatementChunk:
    """MyBatis XML 등에서 추출한 SQL 문장 청크."""
    sql_key: str          # namespace.id
    sql_type: str         # select | insert | update | delete
    start_line: int
    end_line: int
    sql_text: str
    file_path: str


@dataclass
class ConfigSectionChunk:
    """YAML/properties/Spring XML에서 추출한 설정 청크."""
    config_key_path: str
    start_line: int
    end_line: int
    raw_snippet: str
    file_path: str


@dataclass
class ArtifactChunk:
    """비표준 포맷(ejb-jar.xml, entitymodel.xml 등)에서 추출한 청크. REFER_TO 대상(선택)."""
    chunk_id: str
    chunk_type: str   # e.g. "ejb_bean", "entitymodel_entity"
    hint_key: Optional[str] = None
    text: str = ""
    source_path: str = ""
    start_line: int = 0
    end_line: int = 0


# ---------------------------------------------------------------------------
# Step 5 - Table/Column (SQL 파싱 결과)
# ---------------------------------------------------------------------------


@dataclass
class TableRef:
    """SQL에서 참조된 테이블."""
    schema: str
    table: str
    alias: str = ""


@dataclass
class ColumnRef:
    """SQL에서 참조된 컬럼 (Table-HAS_COLUMN용). DBMS와 동일: fqn, name, dtype, description, nullable."""
    table_schema: str
    table_name: str
    column_name: str
    dtype: Optional[str] = None
    description: Optional[str] = None
    nullable: bool = True


@dataclass
class MethodTableEdge:
    """Method or Field → Table 직접 관계 (READS/WRITES, REFER_TO+reads_writes 유도)."""
    method_id: str
    table_schema: str
    table_name: str
    relation: str  # "READS" | "WRITES"
    field_id: Optional[str] = None  # when set, source is Field; else Method


@dataclass
class ReadsWritesEdge:
    """SqlStatement -> Table 관계. relation은 SELECT, INSERT, UPDATE, DELETE 중 하나."""
    sql_key: str
    table_schema: str
    table_name: str
    relation: str  # "SELECT" | "INSERT" | "UPDATE" | "DELETE"


@dataclass
class ReferToEdge:
    """(Method|Field)-[:REFER_TO]->(SqlStatement|ConfigSection|ArtifactChunk|Class) 통합 관계."""
    method_id: str
    target_type: str   # "SqlStatement" | "ConfigSection" | "ArtifactChunk" | "Class"
    target_id: str     # sql_key | config_key_path | chunk_id | class fqn
    evidence: Dict[str, Any] = field(default_factory=dict)
    confidence: float = 1.0
    field_id: Optional[str] = None  # when set, source is Field; else Method


@dataclass
class FkEdge:
    """FK 후보 (SQL join 기반이면 candidate)."""
    from_schema: str
    from_table: str
    from_column: str
    to_schema: str
    to_table: str
    to_column: str
    candidate: bool = True


# ---------------------------------------------------------------------------
# Phase5 참조 후보 및 Unresolved
# ---------------------------------------------------------------------------

REF_CANDIDATE_SQL_LITERAL = "SQL_LITERAL"
REF_CANDIDATE_SQL_KEY_LITERAL = "SQL_KEY_LITERAL"
REF_CANDIDATE_CALL_REF = "CALL_REF"
REF_CANDIDATE_CONFIG_KEY_REF = "CONFIG_KEY_REF"
REF_CANDIDATE_NEW_INSTANCE_REF = "NEW_INSTANCE_REF"
REF_CANDIDATE_DATA_PATH_REF = "DATA_PATH_REF"
REF_CANDIDATE_NAME_REF = "NAME_REF"


@dataclass
class ReferenceCandidate:
    """참조 후보 (Phase5-A 추출). kind별 value/receiver/method_name/className/path 등."""
    kind: str  # SQL_LITERAL | SQL_KEY_LITERAL | CALL_REF | CONFIG_KEY_REF | NEW_INSTANCE_REF | DATA_PATH_REF | NAME_REF
    value: Optional[str] = None
    receiver: Optional[str] = None
    method_name: Optional[str] = None
    className: Optional[str] = None
    path: Optional[str] = None
    line_range: Optional[tuple] = None  # (start_line, end_line)
    code_snippet: Optional[str] = None


@dataclass
class UnresolvedLink:
    """해석 실패 참조 (키 없음, 다중 후보, 동적 키 등). 소스는 Method 또는 Field."""
    method_id: str
    caller_fqn: str
    ref_candidates: List[Any]  # List[ReferenceCandidate] or list of dicts
    file: Optional[str] = None
    line_range: Optional[tuple] = None
    field_id: Optional[str] = None  # when set, source is Field; else Method


# ---------------------------------------------------------------------------
# Step6 LLM → CALLS / USES (METHOD→METHOD, CLASS→CLASS)
# ---------------------------------------------------------------------------


@dataclass
class CallsEdge:
    """(METHOD)-[:CALLS]->(METHOD) 관계. scope='internal'=프로젝트 내, 'external'=외부(미해상)."""
    caller_method_id: str
    callee_method_id: str
    call_code: str = ""  # "target.methodName" 등 (선택)
    scope: str = "internal"  # 'internal' | 'external' (framework/dbms와 동일)


@dataclass
class UsesEdge:
    """(CLASS)-[:USES]->(CLASS) 관계. internal=분석 대상 내, external=외부 클래스(framework와 동일)."""
    from_fqn: str
    to_fqn: str
    scope: str = "internal"  # 'internal' | 'external'
