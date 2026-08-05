"""Shared CodeNode boundary for visible, quarantined LLM quality findings."""
from __future__ import annotations

from collections.abc import Iterable

from graph.schema.graph_nodes import CodeNode
from pipeline.execution.quality_finding_log import record_analysis_findings
from pipeline.models.analysis_finding import AnalysisFinding
from shared.events.runtime_logger import log_warning


def apply_analysis_findings(
    node: CodeNode,
    findings: Iterable[AnalysisFinding],
    *,
    component: str,
) -> None:
    """Persist new findings once and expose a non-success quality status on the node."""
    new = list(findings)
    if new:
        record_analysis_findings(new)
    existing = list((node.extra or {}).get("analysis_findings") or ())
    records = existing + [finding.to_dict() for finding in new]
    if records:
        node.extra["analysis_findings"] = records
        node.extra["analysis_status"] = "completed_with_findings"
        log_warning(
            component,
            "analysis_quality_findings",
            "LLM 후보 일부가 검증되지 않아 trusted graph에서 격리됨",
            code_id=node.id,
            count=len(records),
            by_status={
                status: sum(item["status"] == status for item in records)
                for status in ("rejected", "unverified", "unavailable")
                if any(item["status"] == status for item in records)
            },
            coordinates=sorted({
                row for item in records for row in item.get("coordinates", ())
            }),
        )
    else:
        node.extra.pop("analysis_findings", None)
        node.extra["analysis_status"] = "completed"
