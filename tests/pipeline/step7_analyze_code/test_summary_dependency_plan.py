from __future__ import annotations

import unittest

from pipeline.models.analysis_context import AnalysisContext
from pipeline.steps.step7_analyze_code.analysis_order import (
    bottom_up_rounds,
    plan_summary_dependencies,
)


def _ctx(node_id: str, *callees: str) -> AnalysisContext:
    return AnalysisContext(
        id=node_id,
        signature=f"{node_id}()",
        file_path="neutral.sql",
        start_line=1,
        end_line=1,
        callees=tuple(callees),
    )


def _round_of(plan, contexts: dict[str, AnalysisContext]) -> dict[str, int]:
    rounds = bottom_up_rounds(plan.schedule_edges, contexts)
    return {
        node_id: round_index
        for round_index, round_nodes in enumerate(rounds)
        for node_id in round_nodes
    }


class SummaryDependencyPlanTest(unittest.TestCase):
    def test_dbms_internal_call_waits_for_target_then_releases_owner(self) -> None:
        contexts = {
            "owner": _ctx("owner"),
            "call": _ctx("call", "target"),
            "target": _ctx("target"),
        }

        plan = plan_summary_dependencies(contexts, [("owner", "call")])
        rounds = _round_of(plan, contexts)

        self.assertLess(rounds["target"], rounds["call"])
        self.assertLess(rounds["call"], rounds["owner"])
        self.assertEqual(plan.call_edges, (("call", "target"),))
        self.assertEqual(plan.suppressed_call_edges, ())
        self.assertEqual(plan.cycle_nodes, frozenset())

    def test_dbms_self_recursion_suppresses_only_call_dependency(self) -> None:
        contexts = {
            "owner": _ctx("owner"),
            "call": _ctx("call", "owner"),
        }

        plan = plan_summary_dependencies(contexts, [("owner", "call")])
        rounds = _round_of(plan, contexts)

        self.assertEqual(plan.schedule_edges, (("owner", "call"),))
        self.assertEqual(plan.suppressed_call_edges, (("call", "owner"),))
        self.assertEqual(plan.cycle_nodes, frozenset({"owner", "call"}))
        self.assertLess(rounds["call"], rounds["owner"])

    def test_dbms_mutual_recursion_keeps_children_before_routines(self) -> None:
        contexts = {
            "a": _ctx("a"),
            "call_b": _ctx("call_b", "b"),
            "b": _ctx("b"),
            "call_a": _ctx("call_a", "a"),
        }

        plan = plan_summary_dependencies(
            contexts,
            [("a", "call_b"), ("b", "call_a")],
        )
        rounds = _round_of(plan, contexts)

        self.assertEqual(
            plan.suppressed_call_edges,
            (("call_a", "a"), ("call_b", "b")),
        )
        self.assertEqual(plan.cycle_nodes, frozenset(contexts))
        self.assertLess(rounds["call_a"], rounds["b"])
        self.assertLess(rounds["call_b"], rounds["a"])

    def test_framework_mutual_recursion_stays_in_one_round(self) -> None:
        contexts = {
            "a": _ctx("a", "b"),
            "b": _ctx("b", "a"),
        }

        plan = plan_summary_dependencies(contexts, [])
        rounds = _round_of(plan, contexts)

        self.assertEqual(plan.schedule_edges, ())
        self.assertEqual(
            plan.suppressed_call_edges,
            (("a", "b"), ("b", "a")),
        )
        self.assertEqual(rounds["a"], rounds["b"])

    def test_external_callee_is_not_a_schedule_dependency(self) -> None:
        contexts = {"call": _ctx("call", "external:not-in-analysis")}

        plan = plan_summary_dependencies(contexts, [])

        self.assertEqual(plan.call_edges, ())
        self.assertEqual(plan.schedule_edges, ())
        self.assertEqual(plan.suppressed_call_edges, ())

    def test_output_is_sorted_and_deduplicated(self) -> None:
        contexts = {
            "z": _ctx("z", "b", "a", "b"),
            "a": _ctx("a"),
            "b": _ctx("b"),
        }

        plan = plan_summary_dependencies(
            contexts,
            [("z", "a"), ("z", "a")],
        )

        self.assertEqual(plan.call_edges, (("z", "a"), ("z", "b")))
        self.assertEqual(plan.structural_edges, (("z", "a"),))
        self.assertEqual(plan.schedule_edges, (("z", "a"), ("z", "b")))

    def test_structural_cycle_is_a_fatal_invariant_violation(self) -> None:
        contexts = {"a": _ctx("a"), "b": _ctx("b")}

        with self.assertRaisesRegex(ValueError, "structural summary dependency cycle"):
            plan_summary_dependencies(contexts, [("a", "b"), ("b", "a")])

    def test_context_mapping_key_must_match_context_id(self) -> None:
        contexts = {"wrong-key": _ctx("actual-id")}

        with self.assertRaisesRegex(ValueError, "context key/id mismatch"):
            plan_summary_dependencies(contexts, [])


if __name__ == "__main__":
    unittest.main()
