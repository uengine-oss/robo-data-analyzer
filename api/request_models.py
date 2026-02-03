"""API 요청/응답 Pydantic 모델

router.py에서 사용하는 모든 Pydantic 모델을 정의합니다.
"""

from typing import Optional, List
from pydantic import BaseModel


# =============================================================================
# 파일 타입 감지
# =============================================================================

class FileContent(BaseModel):
    """파일 내용"""
    fileName: str
    content: str


class DetectTypesRequest(BaseModel):
    """파일 타입 감지 요청"""
    files: list[FileContent]


# =============================================================================
# 데이터 리니지
# =============================================================================

class LineageAnalyzeRequest(BaseModel):
    """데이터 리니지 분석 요청"""
    sqlContent: str
    fileName: str = ""
    dbms: str = "oracle"


class LineageNode(BaseModel):
    """리니지 노드"""
    id: str
    name: str
    type: str  # SOURCE, TARGET, ETL
    properties: dict = {}


class LineageEdge(BaseModel):
    """리니지 엣지"""
    id: str
    source: str
    target: str
    type: str  # DATA_FLOW_TO, TRANSFORMS_TO
    properties: dict = {}


class LineageGraphResponse(BaseModel):
    """데이터 리니지 그래프 응답"""
    nodes: List[LineageNode]
    edges: List[LineageEdge]
    stats: dict = {}


# =============================================================================
# 스키마 API
# =============================================================================

class SchemaTableInfo(BaseModel):
    """테이블 정보"""
    name: str
    table_schema: str  # Renamed from 'schema' to avoid BaseModel attribute conflict
    datasource: Optional[str] = ""  # 데이터소스 (예: robo_postgres, ontology)
    description: str
    description_source: Optional[str] = ""  # 설명 출처: ddl, procedure, user
    analyzed_description: Optional[str] = ""  # 프로시저 분석에서 도출된 설명
    column_count: int


class SchemaColumnInfo(BaseModel):
    """컬럼 정보"""
    name: str
    table_name: str
    dtype: str
    nullable: bool
    description: str
    description_source: Optional[str] = ""  # 설명 출처: ddl, procedure, user
    analyzed_description: Optional[str] = ""  # 프로시저 분석에서 도출된 설명


class SchemaRelationshipInfo(BaseModel):
    """테이블 관계 정보"""
    from_table: str
    from_schema: str
    from_column: str
    to_table: str
    to_schema: str
    to_column: str
    relationship_type: str
    description: str


class AddRelationshipRequest(BaseModel):
    """관계 추가 요청"""
    from_table: str
    from_schema: str = ""
    from_column: str
    to_table: str
    to_schema: str = ""
    to_column: str
    relationship_type: str = "FK_TO_TABLE"
    description: str = ""


class SemanticSearchRequest(BaseModel):
    """시멘틱 검색 요청"""
    query: str
    limit: int = 10


class SemanticSearchResult(BaseModel):
    """시멘틱 검색 결과"""
    name: str
    schema: str
    description: str


class ProcedureReferenceInfo(BaseModel):
    """프로시저 참조 정보"""
    procedure_name: str
    procedure_type: str  # PROCEDURE, FUNCTION 등
    start_line: int
    access_type: str  # FROM (읽기), WRITES (쓰기)
    statement_type: Optional[str] = None  # SELECT, INSERT, UPDATE 등
    statement_line: Optional[int] = None
    file_name: Optional[str] = None  # 파일명
    file_directory: Optional[str] = None  # 파일 경로


class StatementSummaryInfo(BaseModel):
    """Statement 요약 정보"""
    start_line: int
    end_line: Optional[int] = None
    statement_type: str
    summary: Optional[str] = None
    ai_description: Optional[str] = None


# =============================================================================
# 파이프라인 제어
# =============================================================================

class PipelineControlRequest(BaseModel):
    """파이프라인 제어 요청"""
    action: str  # "pause" | "resume" | "stop"


# =============================================================================
# 스키마 편집
# =============================================================================

class TableDescriptionUpdateRequest(BaseModel):
    """테이블 설명 업데이트 요청"""
    name: str
    schema: str = "public"
    description: Optional[str] = None


class ColumnDescriptionUpdateRequest(BaseModel):
    """컬럼 설명 업데이트 요청"""
    table_name: str
    table_schema: str = "public"
    column_name: str
    description: Optional[str] = None


class VectorizeRequest(BaseModel):
    """벡터라이징 요청"""
    db_name: Optional[str] = "postgres"
    schema: Optional[str] = None
    include_tables: bool = True
    include_columns: bool = True
    reembed_existing: bool = False
    batch_size: int = 100


# =============================================================================
# DW 스타스키마
# =============================================================================

class DWColumnInfo(BaseModel):
    """DW 컬럼 정보"""
    name: str
    dtype: str = "VARCHAR"
    description: Optional[str] = None
    is_pk: bool = False
    is_fk: bool = False
    fk_target_table: Optional[str] = None  # FK 대상 테이블 (예: "dw.dim_time")


class DWDimensionInfo(BaseModel):
    """DW 디멘전 테이블 정보"""
    name: str  # 테이블명 (예: "dim_time")
    columns: List[DWColumnInfo] = []
    source_tables: List[str] = []  # 원본 테이블 (예: ["RWIS.RDF01HH_TB"])


class DWFactTableInfo(BaseModel):
    """DW 팩트 테이블 정보"""
    name: str  # 테이블명 (예: "fact_turbidity")
    columns: List[DWColumnInfo] = []
    source_tables: List[str] = []


class DWStarSchemaRequest(BaseModel):
    """DW 스타스키마 등록 요청"""
    cube_name: str
    db_name: str = "postgres"
    dw_schema: str = "dw"
    fact_table: DWFactTableInfo
    dimensions: List[DWDimensionInfo] = []
    create_embeddings: bool = True  # 임베딩 자동 생성 여부

