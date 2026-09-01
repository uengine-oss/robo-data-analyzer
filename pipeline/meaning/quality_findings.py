"""Run-owned recording boundary for quarantined code-analysis findings."""
from __future__ import annotations

from collections.abc import Iterable

from graph.schema.graph_nodes import CodeNode
from pipeline.execution.quality_finding_log import record_analysis_findings
from pipeline.models.analysis_finding import AnalysisFinding
from shared.events.runtime_logger import log_warning


def record_code_analysis_findings(
    node: CodeNode,
    findings: Iterable[AnalysisFinding],
    *,
    component: str,
) -> None:
    """Persist excluded or untrusted rows once without copying them to CodeNode."""
    new = list(findings)
    if new:
        record_analysis_findings(new)
    if new:
        records = [finding.to_dict() for finding in new]
        log_warning(
            component,
            "analysis_quality_findings",
            "일부 후보가 제품 공개 대상에서 제외되었거나 검증되지 않아 격리됨",
            code_id=node.id,
            count=len(records),
            by_status={
                status: sum(item["status"] == status for item in records)
                for status in ("excluded", "rejected", "unverified", "unavailable")
                if any(item["status"] == status for item in records)
            },
            coordinates=sorted({
                row for item in records for row in item.get("coordinates", ())
            }),
        )
