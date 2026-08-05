"""Run-owned append-only evidence for quarantined LLM quality findings."""
from __future__ import annotations

import json
import threading
from collections.abc import Iterable

from pipeline.models.analysis_finding import AnalysisFinding
from pipeline.execution.run_output_context import active_run_dir


_LOCK = threading.Lock()


def record_analysis_findings(findings: Iterable[AnalysisFinding]) -> None:
    """Append findings atomically per process; audit-write failure is intentionally fatal."""
    records = [finding.to_dict() for finding in findings]
    if not records:
        return
    run_dir = active_run_dir()
    if run_dir is None:
        return  # standalone unit/helper invocation has no run artifact contract
    path = run_dir / "analysis-findings.jsonl"
    encoded = "".join(
        json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
        for record in records
    )
    with _LOCK:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8", newline="\n") as output:
            output.write(encoded)
