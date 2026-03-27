"""Framework 파이프라인 상태 컨테이너. Dict[str, Any] 대체."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from analyzer.strategy.framework.common.models import (
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


@dataclass
class FrameworkPipelineState:
    """파이프라인 전체 상태. Dict[str, Any] 대체."""

    run_dir: str = ""
    data_dir: str = ""
    lang: str = ""

    # DDL
    ddl_schemas: Any = field(default_factory=set)
    ddl_table_metadata: Dict[str, Any] = field(default_factory=dict)

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
    summary_by_function: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    summary_by_variable: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    summary_by_module: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    user_stories: List[Dict[str, Any]] = field(default_factory=list)

    # DeepAgent
    span_index: Optional[Dict[str, Any]] = None
    chunk_index: Optional[Dict[str, Any]] = None

