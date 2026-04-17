"""Framework 파이프라인 상태 + Phase 컨텍스트.

FrameworkPipelineState: 파이프라인 전체 상태 (Phase 간 데이터 전달)
PhaseContext: 각 Phase 함수에 전달되는 공용 컨텍스트
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from client.neo4j_client import Neo4jClient
from analyzer.strategy.base_analyzer import AnalysisStats
from config.settings import settings

from analyzer.strategy.framework.common.node_models import (
    ArtifactChunk,
    ArtifactTableEdge,
    CallsEdge,
    ModuleTableEdge,
    ColumnRef,
    ConfigSectionChunk,
    VariableFacts,
    FkEdge,
    FunctionFacts,
    FunctionTableEdge,
    ProcessingPlan,
    FileCatalogEntry,
    ReferToEdge,
    ReadsWritesEdge,
    SqlStatementChunk,
    UnresolvedLink,
    UsesEdge,
)
from analyzer.strategy.framework.scanning.profile_models import CodebaseProfile


@dataclass
class FrameworkPipelineState:
    """파이프라인 전체 상태. Dict[str, Any] 대체."""

    run_dir: str = ""
    data_dir: str = ""
    lang: str = ""

    # DDL
    ddl_schemas: Any = field(default_factory=set)
    ddl_table_metadata: Dict[str, Any] = field(default_factory=dict)

    # Step 0: Codebase Profile (Phase 1 서브스텝 — 프레임워크/DB 패턴 요약 + 리소스 분류)
    codebase_profile: Optional[CodebaseProfile] = None

    # Step 1: Scan
    catalog: List[FileCatalogEntry] = field(default_factory=list)
    plan: Optional[ProcessingPlan] = None

    # Step 2: AST
    function_facts_list: List[FunctionFacts] = field(default_factory=list)
    variable_facts_list: List[VariableFacts] = field(default_factory=list)
    module_index_ast: Dict[str, Any] = field(default_factory=dict)
    package_fqns: List[str] = field(default_factory=list)
    symbol_type_index: Dict[str, Dict[str, str]] = field(default_factory=dict)
    function_index: Dict[str, str] = field(default_factory=dict)
    function_index_by_module: Dict[str, Dict[str, List[str]]] = field(default_factory=dict)
    module_name_to_fqn: Dict[str, List[str]] = field(default_factory=dict)
    ref_candidates_by_function: Dict[str, List[Dict[str, Any]]] = field(default_factory=dict)

    # Step 3: Artifacts
    sql_chunks: List[SqlStatementChunk] = field(default_factory=list)
    config_chunks: List[ConfigSectionChunk] = field(default_factory=list)
    artifact_chunks: List[ArtifactChunk] = field(default_factory=list)

    # Step 4: Indexes
    sql_index: Dict[str, SqlStatementChunk] = field(default_factory=dict)
    module_index: Dict[str, Any] = field(default_factory=dict)
    config_index: Dict[str, ConfigSectionChunk] = field(default_factory=dict)
    artifact_index: Dict[str, ArtifactChunk] = field(default_factory=dict)

    # Step 5: Linking
    refer_to_edges: List[ReferToEdge] = field(default_factory=list)
    reads_writes: List[ReadsWritesEdge] = field(default_factory=list)
    fk_edges: List[FkEdge] = field(default_factory=list)
    column_refs: List[ColumnRef] = field(default_factory=list)
    function_table_edges: List[FunctionTableEdge] = field(default_factory=list)
    module_table_edges: List[ModuleTableEdge] = field(default_factory=list)
    artifact_table_edges: List[ArtifactTableEdge] = field(default_factory=list)
    unresolved: List[UnresolvedLink] = field(default_factory=list)
    calls_edges: List[CallsEdge] = field(default_factory=list)
    calls_edges_from_batch: List[CallsEdge] = field(default_factory=list)
    uses_edges: List[UsesEdge] = field(default_factory=list)
    unresolved_calls: List[Any] = field(default_factory=list)
    table_descriptions: Dict[Any, str] = field(default_factory=dict)

    # Step 6: Summary
    analysis_by_function: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    summary_by_variable: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    summary_by_module: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    table_insights_map: Dict[str, Dict[str, Any]] = field(default_factory=dict)

    # DeepAgent
    span_index: Optional[Dict[str, Any]] = None
    chunk_index: Optional[Dict[str, Any]] = None

    def clear_phase_data(self, phase_num: int) -> None:
        """Phase 완료 후 해당 Phase의 중간 데이터를 해제하여 메모리 절약."""
        if phase_num == 1:
            # Phase 1(AST) 완료 후: ref_candidates는 Phase 2에서만 사용
            self.ref_candidates_by_function.clear()
        elif phase_num == 2:
            # Phase 2(Linking) 완료 후: unresolved, calls_from_batch는 Phase 3에서 불필요
            self.unresolved.clear()
            self.calls_edges_from_batch.clear()
            self.unresolved_calls.clear()


@dataclass
class PhaseContext:
    """Phase 함수에 전달되는 공용 컨텍스트.

    각 Phase 함수는 이 객체를 통해 pipeline 상태, Neo4j 클라이언트,
    통계, API 키 등에 접근한다.
    """
    client: Neo4jClient
    state: FrameworkPipelineState
    stats: AnalysisStats
    api_key: str = ""
    locale: str = "ko"
    lang: str = ""
    data_dir: str = ""
    run_dir: Optional[str] = None

