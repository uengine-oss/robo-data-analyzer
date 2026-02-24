"""Core data models for artifacts file processing.

Framework 스키마에 맞춘 데이터 모델입니다.
deep-agents-modernizer의 데이터 모델을 기반으로 하되, Framework 스키마에 맞춰 수정되었습니다.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


class FileCategory(str, enum.Enum):
    """파일 카테고리 (Java 제외)"""
    XML_CONFIG = "xml_config"
    XML_SQLMAP = "xml_sqlmap"
    XML_SPRING = "xml_spring"
    XML_WEB = "xml_web"
    XML_OTHER = "xml_other"
    YAML = "yaml"
    PROPERTIES = "properties"
    JSON = "json"
    BUILD_FILE = "build_file"
    CUSTOM = "custom"
    BINARY = "binary"
    TEXT = "text"
    UNKNOWN = "unknown"


class NodeLabel(str, enum.Enum):
    """노드 레이블 (Framework 스키마에 맞춤)
    
    NOTE: Java 관련 노드는 Framework에서 이미 사용 중이므로 통일된 레이블 사용:
    - JavaClass → CLASS (Phase 5에서 변경)
    - Interface → INTERFACE (Phase 5에서 변경)
    - Method → METHOD (Phase 5에서 변경)
    - Field → FIELD (Phase 5에서 변경)
    - Package → PACKAGE (Phase 5에서 변경)
    """
    PROJECT = "Project"
    MODULE = "Module"
    PACKAGE = "PACKAGE"  # Framework와 통일
    JAVA_CLASS = "CLASS"  # Framework와 통일 (Phase 5에서 사용)
    INTERFACE = "INTERFACE"  # Framework와 통일 (Phase 5에서 사용)
    METHOD = "METHOD"  # Framework와 통일 (Phase 5에서 사용)
    FIELD = "FIELD"  # Framework와 통일 (Phase 5에서 사용)
    SQL_MAP = "SqlMap"
    SQL_STATEMENT = "SqlStatement"
    SPRING_BEAN = "SpringBean"
    CONFIG_FILE = "ConfigFile"
    CONFIG_SECTION = "ConfigSection"
    RESOURCE = "Resource"
    TABLE = "Table"
    COLUMN = "Column"
    DTO = "DTO"
    TYPE_ALIAS = "TypeAlias"


class EdgeType(str, enum.Enum):
    """관계 타입 (Framework 스키마에 맞춤)
    
    NOTE: 일부 관계는 Framework와 통일:
    - DEPENDS_ON → DEPENDENCY (Phase 4, 5에서 변경)
    - CALLS_METHOD → CALLS (Phase 4, 5에서 변경)
    """
    CONTAINS = "CONTAINS"
    IMPORTS = "IMPORTS"
    EXTENDS = "EXTENDS"
    IMPLEMENTS = "IMPLEMENTS"
    DEPENDS_ON = "DEPENDENCY"  # Framework와 통일 (Phase 4, 5에서 사용)
    MAPS_TO_SQL = "MAPS_TO_SQL"
    MAPS_TO_TABLE = "MAPS_TO_TABLE"
    REFERENCES_BEAN = "REFERENCES_BEAN"
    CONFIGURES = "CONFIGURES"
    MATCHES_BY_NAME = "MATCHES_BY_NAME"
    CALLS = "CALLS"
    USES = "USES"
    HAS_METHOD = "HAS_METHOD"
    HAS_COLUMN = "HAS_COLUMN"
    SELECT = "SELECT"
    INSERT = "INSERT"
    UPDATE = "UPDATE"
    DELETE = "DELETE"
    ALIAS_OF = "ALIAS_OF"
    PARAMETER_TYPE = "PARAMETER_TYPE"
    RESULT_TYPE = "RESULT_TYPE"
    CALLS_SQL = "CALLS_SQL"
    CALLS_METHOD = "CALLS"  # Framework와 통일 (Phase 4, 5에서 사용)


class PhaseStatus(str, enum.Enum):
    """Phase 실행 상태"""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass
class FileInfo:
    """파일 정보"""
    path: Path
    relative_path: str
    extension: str
    size: int
    encoding: str = "utf-8"
    mime_type: str = ""
    category: FileCategory = FileCategory.UNKNOWN
    detected_type: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ProjectStructure:
    """프로젝트 구조 정보"""
    root_path: Path
    files: list[FileInfo] = field(default_factory=list)
    directory_tree: dict[str, Any] = field(default_factory=dict)
    build_tool: str = ""
    framework_profile: dict[str, Any] = field(default_factory=dict)
    modules: list[str] = field(default_factory=list)
    java_version: str = ""
    total_files: int = 0
    file_type_counts: dict[str, int] = field(default_factory=dict)


@dataclass
class SchemaDefinition:
    """파일 형식 스키마 정의"""
    format_name: str
    file_category: FileCategory
    schema_type: str  # "xsd", "json_schema", "ast", "custom"
    schema_content: dict[str, Any] = field(default_factory=dict)
    sample_files: list[str] = field(default_factory=list)
    parser_code: str = ""
    confidence: float = 1.0


@dataclass
class GeneratedCode:
    """LLM 생성 코드 아티팩트"""
    code_id: str
    code_type: str  # "chunker" | "relationship" | "ontology_mapper"
    category: str
    source_code: str = ""
    function_name: str = ""
    schema_hash: str = ""
    generated_at: str = ""


@dataclass
class FormatRegistry:
    """발견된 모든 파일 형식 및 스키마 레지스트리"""
    schemas: dict[str, SchemaDefinition] = field(default_factory=dict)
    parser_codes: dict[str, str] = field(default_factory=dict)
    similarity_map: dict[str, list[str]] = field(default_factory=dict)
    generated_codes: dict[str, GeneratedCode] = field(default_factory=dict)


@dataclass
class ChunkMetadata:
    """코드/설정 청크 메타데이터"""
    source_file: str
    chunk_type: str  # "class", "method", "field", "sql_statement", "bean", "section"
    start_line: int = 0
    end_line: int = 0
    parent_chunk_id: str = ""
    properties: dict[str, Any] = field(default_factory=dict)


@dataclass
class Chunk:
    """의미 있는 코드/설정 청크"""
    chunk_id: str
    content: str
    metadata: ChunkMetadata
    sub_chunks: list[Chunk] = field(default_factory=list)


@dataclass
class ChunkingStrategy:
    """파일 타입별 청킹 전략"""
    file_category: FileCategory
    chunk_unit: str
    strategy_description: str
    chunker_code: str = ""
    metadata_preserved: list[str] = field(default_factory=list)


@dataclass
class Relationship:
    """두 엔티티 간의 관계"""
    source_id: str
    target_id: str
    edge_type: EdgeType
    confidence: float = 1.0
    properties: dict[str, Any] = field(default_factory=dict)
    discovery_method: str = ""  # "explicit", "naming_convention", "config_ref", etc.


@dataclass
class PatternRule:
    """프로젝트에서 발견된 메타 레벨 패턴 규칙"""
    rule_id: str
    description: str
    pattern_type: str  # "naming", "directory", "config_ref"
    regex_pattern: str = ""
    examples: list[str] = field(default_factory=list)
    confidence: float = 1.0


@dataclass
class GraphNode:
    """Neo4j에 삽입할 노드"""
    node_id: str
    label: str  # Framework 스키마에 맞춘 문자열 (예: "CLASS", "PACKAGE", "SqlStatement")
    properties: dict[str, Any] = field(default_factory=dict)


@dataclass
class GraphEdge:
    """Neo4j에 삽입할 엣지"""
    source_id: str
    target_id: str
    edge_type: str  # Framework 스키마에 맞춘 문자열 (예: "DEPENDENCY", "CALLS", "MAPS_TO_SQL")
    properties: dict[str, Any] = field(default_factory=dict)


@dataclass
class PhaseResult:
    """Phase 실행 결과"""
    phase_name: str
    status: PhaseStatus
    data: dict[str, Any] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass
class ValidationReport:
    """검증 Phase 리포트"""
    completeness_score: float = 0.0
    connectivity_score: float = 0.0
    consistency_score: float = 0.0
    orphan_nodes: list[str] = field(default_factory=list)
    broken_references: list[dict[str, str]] = field(default_factory=list)
    low_confidence_relations: list[Relationship] = field(default_factory=list)
    total_nodes: int = 0
    total_edges: int = 0
    issues: list[str] = field(default_factory=list)
