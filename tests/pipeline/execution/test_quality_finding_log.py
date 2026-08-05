from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from pipeline.execution.quality_finding_log import record_analysis_findings
from pipeline.execution.run_executor import _summarize_analysis_findings
from pipeline.execution.run_output_context import reset_run_dir, set_run_dir
from pipeline.models.analysis_finding import AnalysisFinding
from graph.nodes.code_node import to_summary_item
from graph.schema.graph_nodes import CodeNode


class QualityFindingLedgerTest(unittest.TestCase):
    def test_records_and_summarizes_visible_partial_quality(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            run_dir = Path(temp)
            token = set_run_dir(run_dir)
            try:
                record_analysis_findings([
                    AnalysisFinding(
                        concern="rules",
                        status="rejected",
                        target_id="fn::R1",
                        coordinates=(1,),
                        reasons=("meaning differs",),
                    ),
                    AnalysisFinding(
                        concern="rules",
                        status="unavailable",
                        target_id="fn::R2",
                        coordinates=(2,),
                        reasons=("schema",),
                    ),
                ])
            finally:
                reset_run_dir(token)

            returned = _summarize_analysis_findings(run_dir)
            summary = json.loads(
                (run_dir / "analysis_findings_summary.json").read_text(encoding="utf-8")
            )
            self.assertEqual("completed_with_findings", summary["run_quality_status"])
            self.assertEqual(2, summary["total"])
            self.assertEqual({"rejected": 1, "unavailable": 1}, summary["by_status"])
            self.assertEqual(2, summary["affected_targets"])
            self.assertEqual(summary, returned)

    def test_no_findings_is_explicit_completed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            run_dir = Path(temp)
            returned = _summarize_analysis_findings(run_dir)
            summary = json.loads(
                (run_dir / "analysis_findings_summary.json").read_text(encoding="utf-8")
            )
            self.assertEqual("completed", summary["run_quality_status"])
            self.assertEqual(0, summary["total"])
            self.assertEqual(summary, returned)

    def test_corrupt_ledger_is_fatal_not_silent(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            run_dir = Path(temp)
            (run_dir / "analysis-findings.jsonl").write_text("{broken\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "invalid analysis finding evidence"):
                _summarize_analysis_findings(run_dir)

    def test_code_node_persists_status_and_findings_without_rule_nodes(self) -> None:
        node = CodeNode(
            id="fn", name="f", signature="f()", file_path="sample.c",
            start_line=1, end_line=1, label="FUNCTION", summary="summary",
        )
        node.extra = {
            "analysis_status": "completed_with_findings",
            "analysis_findings": [{
                "concern": "rules",
                "status": "rejected",
                "target_id": "R1",
                "coordinates": [1],
                "reasons": ["bad"],
                "candidate": None,
                "evidence_rule": "code_rules_examples",
            }],
        }
        item = to_summary_item(node)
        self.assertEqual("completed_with_findings", item["analysis_status"])
        self.assertEqual("rejected", json.loads(item["analysis_findings_json"])[0]["status"])


if __name__ == "__main__":
    unittest.main()
