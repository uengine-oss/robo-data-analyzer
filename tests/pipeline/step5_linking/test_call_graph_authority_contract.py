"""Only deterministic syntax/skill evidence may change the canonical CALLS graph."""
from __future__ import annotations

import unittest
from pathlib import Path

from graph.schema.graph_nodes import CodeNode
from pipeline.steps.step5_linking.link_calls.call_classifier import classify_call_sites


ROOT = Path(__file__).resolve().parents[3]


class CallGraphAuthorityContractTest(unittest.TestCase):
    def test_link_step_has_no_llm_edge_deletion_boundary(self) -> None:
        source = (
            ROOT
            / "pipeline/steps/step5_linking/link_calls/link_calls_by_name_step.py"
        ).read_text(encoding="utf-8")

        for forbidden in (
            "_maybe_review_externals",
            "review_external_calls",
            "call_review_enabled",
            "external_call_review",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, source)

    def test_call_review_authority_artifacts_are_removed(self) -> None:
        self.assertFalse((ROOT / "agents/call_review.py").exists())
        self.assertFalse((ROOT / "agents/prompts/call_review.md").exists())

        contracts = (ROOT / "agents/contracts.py").read_text(encoding="utf-8")
        settings = (ROOT / "shared/config/settings.py").read_text(encoding="utf-8")
        for forbidden in ("CallDropVerdict", "ExternalCallReview"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, contracts)
        for forbidden in (
            "call_review_enabled",
            "call_review_max_calls",
            "call_review_max_drop_ratio",
            "ROBO_AGENT_CALL_REVIEW",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, settings)

    def test_actual_contradiction_population_remains_external(self) -> None:
        caller = CodeNode(
            id="code:sample.c:owner",
            signature="void owner(void)",
            file_path="sample.c",
            start_line=1,
            end_line=100,
            label="FUNCTION",
            name="owner",
        )
        population = {
            "PFM_DSP": 39,
            "PFM_MSGN": 3,
            "PFM_MSG_CLEAR": 2,
            "PFM_ERRB": 2,
        }
        call_sites = [
            {
                "call_id": f"site-{index}",
                "caller_id": caller.id,
                "owner_status": "resolved",
                "call_text": name,
                "callee_kind": "named",
                "source_id": "source:missing-source.c",
                "presence_status": "active",
                "presence": {"status": "active", "condition": None,
                             "configurationId": None, "provenance": "unconditional"},
                "file": "missing-source.c",
                "line": index + 1,
                "range": {"char_offset": index * 10, "char_length": 8},
                "callee_range": {"char_offset": index * 10, "char_length": 4},
                "argument_ranges": [],
                "grammar_rule": "postfixExpression",
            }
            for index, name in enumerate(
                name
                for name, count in population.items()
                for _ in range(count)
            )
        ]

        result = classify_call_sites(call_sites, [caller])

        self.assertEqual(46, len(result.edges))
        self.assertEqual(
            population,
            {
                name: sum(edge.callee_id == name for edge in result.edges)
                for name in population
            },
        )
        self.assertTrue(all(edge.scope == "external" for edge in result.edges))


if __name__ == "__main__":
    unittest.main()
