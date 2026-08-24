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
    @staticmethod
    def _call_row(call_id: str, outcome: str, reason: str) -> dict:
        return {
            "call_id": call_id,
            "ordinal": int(call_id.rsplit("-", 1)[-1]) - 1,
            "source_id": "source:a.c", "caller_id": "code:f",
            "owner_status": "resolved", "file": "a.c", "line": 3,
            "range": {"char_offset": 12, "char_length": 8},
            "callee_range": {"char_offset": 12, "char_length": 4},
            "argument_ranges": [], "callee_kind": "named", "terminal_name": "work",
            "grammar_rule": "postfixExpression", "presence_status": "active",
            "presence": {"status": "active", "condition": None,
                         "configurationId": None, "provenance": "unconditional"},
            "configured_preprocessing_required": False,
            "resolution_status": (
                "external" if outcome == "preserved" else "unsupported"
            ),
            "adapter_schema": "language-neutral-resolution/v1",
            "outcome": outcome, "reason": reason,
            "scope": None, "callee_id": "",
            "external_identity_key": "e" * 64 if outcome == "preserved" else "",
            "declaration_fact_id": "", "candidate_fact_ids": [],
            "preprocessing_evidence_ids": [], "macro_fact_ids": [],
        }

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

    def test_unresolved_call_occurrence_is_visible_run_finding(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            run_dir = Path(temp)
            returned = _summarize_analysis_findings(run_dir, call_resolution_ledger=[
                self._call_row("call-1", "unresolved", "missing_trace"),
                self._call_row("call-2", "preserved", "stdlib"),
            ])

        self.assertEqual("completed_with_findings", returned["run_quality_status"])
        self.assertEqual(2, returned["call_resolution"]["total"])
        self.assertEqual(1, returned["call_resolution"]["unresolved"])
        self.assertEqual({"unresolved": 1, "preserved": 1},
                         returned["call_resolution"]["by_outcome"])

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
