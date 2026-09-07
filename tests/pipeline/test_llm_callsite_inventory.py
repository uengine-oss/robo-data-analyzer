"""Fail closed when a production path adds a model call without contract review."""
from __future__ import annotations

import ast
from collections import Counter
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]


def _named_calls(root: Path, function_name: str) -> Counter[tuple[str, str]]:
    calls: Counter[tuple[str, str]] = Counter()
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            called = (
                node.func.id if isinstance(node.func, ast.Name)
                else node.func.attr if isinstance(node.func, ast.Attribute)
                else ""
            )
            if called != function_name:
                continue
            rule_name = next(
                (keyword.value for keyword in node.keywords if keyword.arg == "rule_name"),
                None,
            )
            if isinstance(rule_name, ast.Constant):
                label = str(rule_name.value)
            elif isinstance(rule_name, ast.Name):
                label = f"<{rule_name.id}>"
            else:
                label = "<agent>"
            calls[(path.relative_to(ROOT).as_posix(), label)] += 1
    return calls


class ProductionLlmCallsiteInventoryTest(unittest.TestCase):
    def test_simple_yaml_call_sites_are_exactly_the_reviewed_set(self) -> None:
        actual = _named_calls(ROOT / "agents", "run_yaml_rule")
        actual.update(_named_calls(ROOT / "pipeline", "run_yaml_rule"))
        self.assertEqual(Counter({
            ("agents/residual_agent.py", "<_RESIDUAL_RULE>"): 1,
            (
                "pipeline/meaning/batch_analyze.py",
                "code_summary_node",
            ): 1,
            (
                "pipeline/meaning/batch_analyze.py",
                "code_rule_meanings_batch",
            ): 1,
            (
                "pipeline/meaning/meaning_requests.py",
                "code_summary",
            ): 1,
            (
                "pipeline/meaning/meaning_requests.py",
                "code_rule_meanings",
            ): 1,
            (
                "pipeline/stages/output/enrich_table_description_step.py",
                "table_description",
            ): 1,
        }), actual)

    def test_tool_using_agent_entrypoints_are_exactly_scan_and_external(self) -> None:
        self.assertEqual(Counter({
            ("agents/external_agent.py", "<agent>"): 1,
            ("agents/scan_agent.py", "<agent>"): 1,
        }), _named_calls(ROOT / "agents", "run_structured_agent"))

    def test_search_has_one_reviewed_structured_model_boundary(self) -> None:
        self.assertEqual(Counter({
        }), _named_calls(ROOT / "search", "invoke_structured"))


if __name__ == "__main__":
    unittest.main()
