"""인메모리 그래프 또는 Neo4j 쓰기 어댑터 (선택)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from exp.models import (
    CallsEdge,
    ConfigSectionChunk,
    FkEdge,
    MethodFacts,
    ReadsWritesEdge,
    ReferToEdge,
    SqlStatementChunk,
    UsesEdge,
)


@dataclass
class InMemoryGraph:
    """메모리 그래프. 노드/엣지 리스트 보관."""

    methods: List[MethodFacts] = field(default_factory=list)
    sql_chunks: List[SqlStatementChunk] = field(default_factory=list)
    config_chunks: List[ConfigSectionChunk] = field(default_factory=list)
    refer_to_edges: List[ReferToEdge] = field(default_factory=list)
    reads_writes: List[ReadsWritesEdge] = field(default_factory=list)
    fk_edges: List[FkEdge] = field(default_factory=list)
    summary_by_method: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    calls_edges: List[CallsEdge] = field(default_factory=list)
    uses_edges: List[UsesEdge] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "methods": [
                {
                    "method_id": m.method_id,
                    "fqn": m.fqn,
                    "start_line": m.start_line,
                    "end_line": m.end_line,
                }
                for m in self.methods
            ],
            "sql_statements": [
                {"sql_key": s.sql_key, "sql_type": s.sql_type}
                for s in self.sql_chunks
            ],
            "refer_to_edges": [
                {"method_id": e.method_id, "target_type": e.target_type, "target_id": e.target_id}
                for e in self.refer_to_edges
            ],
            "reads_writes": [
                {"sql_key": e.sql_key, "table": e.table_name, "relation": e.relation}
                for e in self.reads_writes
            ],
            "summaries": self.summary_by_method,
            "calls_edges": [
                {"caller_method_id": e.caller_method_id, "callee_method_id": e.callee_method_id}
                for e in self.calls_edges
            ],
            "uses_edges": [
                {"from_fqn": e.from_fqn, "to_fqn": e.to_fqn}
                for e in self.uses_edges
            ],
        }


def build_in_memory_graph(
    method_facts_list: List[MethodFacts],
    sql_chunks: List[SqlStatementChunk],
    config_chunks: List[ConfigSectionChunk],
    refer_to_edges: List[ReferToEdge],
    reads_writes: List[ReadsWritesEdge],
    fk_edges: List[FkEdge],
    summary_by_method: Dict[str, Dict[str, Any]],
    calls_edges: Optional[List[CallsEdge]] = None,
    uses_edges: Optional[List[UsesEdge]] = None,
) -> InMemoryGraph:
    """Step 결과를 인메모리 그래프로 구성."""
    return InMemoryGraph(
        methods=method_facts_list,
        sql_chunks=sql_chunks,
        config_chunks=config_chunks,
        refer_to_edges=refer_to_edges,
        reads_writes=reads_writes,
        fk_edges=fk_edges,
        summary_by_method=summary_by_method,
        calls_edges=calls_edges or [],
        uses_edges=uses_edges or [],
    )
