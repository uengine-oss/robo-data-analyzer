"""Framework 전략 데이터 모델.

Edge 타입, FunctionFacts, InMemoryGraph 등.
build_in_memory_graph() 팩토리 함수도 이 파일에 위치 — InMemoryGraph와 결합도가 높아
모델 정의와 함께 두는 것이 응집도 면에서 유리하기 때문.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Ontology 상수 (Neo4j 미저장 내부 노드 타입)
# ---------------------------------------------------------------------------

NODE_VARIABLE = "VARIABLE"
NODE_METHOD_CALL = "METHOD_CALL"
NODE_NEW_INSTANCE = "NEW_INSTANCE"


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
    category: str  # source_ast | sql | entity | config | etc
    hash_val: str = ""


@dataclass
class ProcessingPlan:
    """처리 계획: 분류된 작업 목록 (범용)."""
    source_ast_jobs: List[str] = field(default_factory=list)   # 소스코드 파일 (모든 언어)
    sql_jobs: List[str] = field(default_factory=list)          # SQL 매핑 XML
    entity_jobs: List[str] = field(default_factory=list)       # 엔티티 정의 XML
    config_jobs: List[str] = field(default_factory=list)       # 설정 파일 (yaml, properties, json, 일반 xml)
    docs_jobs: List[str] = field(default_factory=list)         # 비정형 문서

    def iter_jobs(self):
        """모든 job 유형을 (category, paths) 쌍으로 순회."""
        yield "source_ast", self.source_ast_jobs
        yield "sql", self.sql_jobs
        yield "entity", self.entity_jobs
        yield "config", self.config_jobs
        yield "docs", self.docs_jobs


# ---------------------------------------------------------------------------
# Step 2
# ---------------------------------------------------------------------------


@dataclass
class VariableFacts:
    """변수 단위 팩트 (AST에서 추출).

    값 없음(예: 변수 type 정보가 없음)은 빈 문자열로 표현. None 사용 금지 (호출자 폴백 회피).
    """
    variable_id: str
    module_key: str
    name: str
    start_line: int
    end_line: int
    variable_type: str = ""
    code_ref: str = ""
    comment: str = ""
    annotations: str = ""
    code: str = ""
    # 선언부 초기화식 텍스트 (ANTLR 의 node.initValue) — Analyzer 가 식별자 추출하여 INIT_BY 매칭
    init_value: str = ""
    # AST 의 원래 노드 타입 (VARIABLE / GLOBAL_VARIABLE / PARAMETER / DEFINE / FIELD 등).
    # variable_linker 의 scope 결정 (함수 스코프 vs 와일드카드) 에 사용.
    kind: str = ""


@dataclass
class InitByEdge:
    """(VARIABLE)-[:INIT_BY]->(VARIABLE|CONSTANT) 초기화 관계.

    변수가 다른 변수 또는 상수의 값으로 초기화될 때 생성한다.
    예: int limit = MAX_LOANS; → (VARIABLE:limit)-[:INIT_BY]->(CONSTANT:MAX_LOANS)

    컨텍스트 역추적 시 REFERENCES + INIT_BY를 쌍으로 조회하여
    함수가 참조하는 변수의 초기화 출처를 추적한다.
    """
    variable_id: str      # 초기화되는 변수 (좌변)
    init_source_id: str   # 초기화 출처 변수/상수 (우변)


@dataclass
class FunctionFacts:
    """함수 단위 팩트 (AST에서 추출).

    값 없음은 빈 문자열로 표현. None 사용 금지 (호출자 폴백 회피).
    """
    function_id: str
    fqn: str
    signature: str
    start_line: int
    end_line: int
    annotations: str = ""
    comment: str = ""
    return_type: str = ""
    params: str = ""
    code_ref: str = ""   # file_path + line_range 참조
    module_key: str = ""
    package_name: str = ""
    code: str = ""


# ---------------------------------------------------------------------------
# ChunkBundle (링킹/분석용, analysis JSON 있는 파일만)
# ---------------------------------------------------------------------------


@dataclass
class ChunkBundleFile:
    """ChunkBundle 메타: FILE 단위."""
    file_path: str
    file_hash: str = ""
    encoding: str = ""


@dataclass
class ChunkBundleMethod:
    """ChunkBundle 내 METHOD: function_id, span, function_code(slice+라인번호, 빈 문자열 가능)."""
    function_id: str
    start_line: int
    end_line: int
    function_code: str = ""


@dataclass
class ChunkBundleFunctionCall:
    """ChunkBundle 내 FUNCTION_CALL: parent_function_id, call_id, span, call_code(필수)."""
    parent_function_id: str
    call_id: str
    start_line: int
    end_line: int
    call_code: str  # slice로 생성, 라인번호 포함



@dataclass
class ChunkBundleLocalVar:
    """ChunkBundle 내 지역변수 (노드 생성 안 됨, LLM 분석용)."""
    parent_function_id: str
    var_name: str
    var_type: str = ""
    start_line: int = 0
    end_line: int = 0
    flags: Dict[str, Any] = field(default_factory=dict)  # e.g. initializerContainsMethodCall
    initializer_code: str = ""  # slice when flags.initializerContainsMethodCall


@dataclass
class ChunkBundleMemberVar:
    """ChunkBundle 내 멤버변수/필드 (Neo4j VARIABLE/CONSTANT 노드로 생성됨)."""
    variable_id: str
    start_line: int
    end_line: int
    variable_code: str = ""


@dataclass
class ChunkBundle:
    """파일 단위 ChunkBundle: 메타 + METHOD/METHOD_CALL/LOCAL_VAR/MEMBER_VAR 목록."""
    file_meta: ChunkBundleFile
    methods: List[ChunkBundleMethod] = field(default_factory=list)
    function_calls: List[ChunkBundleFunctionCall] = field(default_factory=list)
    local_vars: List[ChunkBundleLocalVar] = field(default_factory=list)
    member_vars: List[ChunkBundleMemberVar] = field(default_factory=list)


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
    chunk_type: str   # e.g. "entity", "cmp_mapping"
    hint_key: str = ""
    text: str = ""
    source_path: str = ""
    start_line: int = 0
    end_line: int = 0
    # EJB XML에서 추출한 관련 모듈 FQN 목록 (ejb-class, home, remote 등)
    related_module_fqns: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Step 5 - Table/Column (SQL 파싱 결과)
# ---------------------------------------------------------------------------


@dataclass
class ColumnRef:
    """SQL에서 참조된 컬럼 (Table-HAS_COLUMN용). DBMS와 동일: fqn, name, dtype, description, nullable."""
    table_schema: str
    table_name: str
    column_name: str
    dtype: str = ""
    description: str = ""
    nullable: bool = True


@dataclass
class FunctionTableEdge:
    """Function or Variable → Table 직접 관계 (READS/WRITES, REFER_TO+reads_writes 유도)."""
    function_id: str
    table_schema: str
    table_name: str
    relation: str  # "READS" | "WRITES"
    variable_id: Optional[str] = None
    op: str = ""  # "SELECT" | "INSERT" | "UPDATE" | "DELETE" | "MERGE"


@dataclass
class ModuleTableEdge:
    """엔티티 Module → Table 출처 연결. (MODULE)-[:REFER_TO]->(TABLE). entity::FQN 전용."""
    module_fqn: str
    table_schema: str
    table_name: str


@dataclass
class ArtifactTableEdge:
    """EJB XML(artifact) → Table 출처 연결. (ARTIFACT_CHUNK)-[:REFER_TO]->(TABLE). entity_xml:: 전용, 읽기쓰기 없이 참조만."""
    chunk_id: str
    table_schema: str
    table_name: str


@dataclass
class ReadsWritesEdge:
    """SqlStatement -> Table 관계. relation은 SELECT, INSERT, UPDATE, DELETE 중 하나."""
    sql_key: str
    table_schema: str
    table_name: str
    relation: str  # "SELECT" | "INSERT" | "UPDATE" | "DELETE"


@dataclass
class ReferToEdge:
    """(Function|Variable)-[:REFER_TO]->(SqlStatement|ConfigSection|ArtifactChunk|Class) 통합 관계."""
    function_id: str
    target_type: str   # "SqlStatement" | "ConfigSection" | "ArtifactChunk" | "Class"
    target_id: str     # sql_key | config_key_path | chunk_id | class fqn
    evidence: Dict[str, Any] = field(default_factory=dict)
    confidence: float = 1.0
    variable_id: Optional[str] = None  # when set, source is Variable; else Function


@dataclass
class FkEdge:
    """FK 후보 (SQL join 기반이면 candidate).

    source: FK 정보의 출처.
      - "ddl": DDL 스키마에서 추출한 확정 FK
      - "code": 소스코드 SQL join 분석으로 추론한 후보 FK
    """
    from_schema: str
    from_table: str
    from_column: str
    to_schema: str
    to_table: str
    to_column: str
    candidate: bool = True
    source: str = "code"


# ---------------------------------------------------------------------------
# Phase5 참조 후보 및 Unresolved
# ---------------------------------------------------------------------------


@dataclass
class ReferenceCandidate:
    """참조 후보 (Phase5-A 추출). kind별 value/receiver/function_name/moduleName/path 등."""
    kind: str  # SQL_LITERAL | SQL_KEY_LITERAL | CALL_REF | CONFIG_KEY_REF | NEW_INSTANCE_REF | DATA_PATH_REF | NAME_REF
    value: Optional[str] = None
    receiver: Optional[str] = None
    function_name: Optional[str] = None
    moduleName: Optional[str] = None
    path: Optional[str] = None
    line_range: Optional[tuple] = None  # (start_line, end_line)
    code_snippet: Optional[str] = None


@dataclass
class UnresolvedLink:
    """해석 실패 참조 (키 없음, 다중 후보, 동적 키 등). 소스는 Function 또는 Variable."""
    function_id: str
    caller_fqn: str
    ref_candidates: List[Any]  # List[ReferenceCandidate] or list of dicts
    file: Optional[str] = None
    line_range: Optional[tuple] = None
    variable_id: Optional[str] = None  # when set, source is Variable; else Function


# ---------------------------------------------------------------------------
# Step6 LLM → CALLS / USES (METHOD→METHOD, MODULE→MODULE)
# ---------------------------------------------------------------------------


@dataclass
class CallsEdge:
    """(FUNCTION)-[:CALLS]->(FUNCTION) 관계. scope='internal'=프로젝트 내, 'external'=외부(미해상)."""
    caller_function_id: str
    callee_function_id: str
    call_code: str = ""  # "target.methodName" 등 (선택)
    scope: str = "internal"  # 'internal' | 'external' (framework/dbms와 동일)


@dataclass
class UsesEdge:
    """(MODULE)-[:USES]->(MODULE) 관계. internal=분석 대상 내, external=외부 모듈(framework와 동일)."""
    from_fqn: str
    to_fqn: str
    scope: str = "internal"  # 'internal' | 'external'

