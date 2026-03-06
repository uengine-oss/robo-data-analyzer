"""Framework 파이프라인 상태 컨테이너. Dict[str, Any] 대체."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from analyzer.strategy.framework.common.models import (
    ArtifactChunk,
    ArtifactTableEdge,
    CallsEdge,
    ClassTableEdge,
    ColumnRef,
    ConfigSectionChunk,
    FieldFacts,
    FkEdge,
    MethodFacts,
    MethodTableEdge,
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

    # Step 1: Scan
    catalog: List[FileCatalogEntry] = field(default_factory=list)
    plan: Optional[ProcessingPlan] = None

    # Step 2: AST
    method_facts_list: List[MethodFacts] = field(default_factory=list)
    field_facts_list: List[FieldFacts] = field(default_factory=list)
    class_index_ast: Dict[str, Any] = field(default_factory=dict)
    package_fqns: List[str] = field(default_factory=list)
    symbol_type_index: Dict[str, Dict[str, str]] = field(default_factory=dict)
    method_index: Dict[str, str] = field(default_factory=dict)
    method_index_by_class: Dict[str, Dict[str, List[str]]] = field(default_factory=dict)
    class_index_simple: Dict[str, List[str]] = field(default_factory=dict)
    ref_candidates_by_method: Dict[str, List[Dict[str, Any]]] = field(default_factory=dict)

    # Step 3: Artifacts
    sql_chunks: List[SqlStatementChunk] = field(default_factory=list)
    config_chunks: List[ConfigSectionChunk] = field(default_factory=list)
    artifact_chunks: List[ArtifactChunk] = field(default_factory=list)

    # Step 4: Indexes
    sql_index: Dict[str, SqlStatementChunk] = field(default_factory=dict)
    class_index: Dict[str, Any] = field(default_factory=dict)
    config_index: Dict[str, ConfigSectionChunk] = field(default_factory=dict)
    artifact_index: Dict[str, ArtifactChunk] = field(default_factory=dict)

    # Step 5: Linking
    refer_to_edges: List[ReferToEdge] = field(default_factory=list)
    reads_writes: List[ReadsWritesEdge] = field(default_factory=list)
    fk_edges: List[FkEdge] = field(default_factory=list)
    column_refs: List[ColumnRef] = field(default_factory=list)
    method_table_edges: List[MethodTableEdge] = field(default_factory=list)
    class_table_edges: List[ClassTableEdge] = field(default_factory=list)
    artifact_table_edges: List[ArtifactTableEdge] = field(default_factory=list)
    unresolved: List[UnresolvedLink] = field(default_factory=list)
    calls_edges: List[CallsEdge] = field(default_factory=list)
    calls_edges_from_batch: List[CallsEdge] = field(default_factory=list)
    uses_edges: List[UsesEdge] = field(default_factory=list)
    unresolved_calls: List[Any] = field(default_factory=list)
    table_descriptions: Dict[Any, str] = field(default_factory=dict)

    # Step 6: Summary
    summary_by_method: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    summary_by_field: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    summary_by_class: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    user_stories: List[Dict[str, Any]] = field(default_factory=list)

    # DeepAgent
    span_index: Optional[Dict[str, Any]] = None
    chunk_index: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        """하위호환: Dict[str, Any] 형태로 변환."""
        from dataclasses import fields as dc_fields
        return {f.name: getattr(self, f.name) for f in dc_fields(self)}

    def update(self, d: Dict[str, Any]) -> None:
        """하위호환: dict.update() 스타일."""
        for k, v in d.items():
            if hasattr(self, k):
                setattr(self, k, v)

    def get(self, key: str, default: Any = None) -> Any:
        """하위호환: dict.get() 스타일."""
        return getattr(self, key, default)

    def setdefault(self, key: str, default: Any = None) -> Any:
        """하위호환: dict.setdefault() 스타일."""
        val = getattr(self, key, None)
        if val is None:
            setattr(self, key, default)
            return default
        return val

    def __getitem__(self, key: str) -> Any:
        """하위호환: dict[key] 스타일."""
        return getattr(self, key)

    def __setitem__(self, key: str, value: Any) -> None:
        """하위호환: dict[key] = value 스타일."""
        setattr(self, key, value)

    def __contains__(self, key: str) -> bool:
        """하위호환: key in state 스타일."""
        return hasattr(self, key)
