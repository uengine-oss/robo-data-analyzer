from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock, patch

from agents.agent_contracts import ScanProposal
from agents.scan_agent import SkillSelectionAmbiguityError, run_scan_agent
from graph.schema.graph_nodes import CodeNode, Table
from llm.usage.cost_budget import CostBudget
from pipeline.checks.source_lexing import split_source_for_linking


def _node(path: Path, end_line: int = 8) -> CodeNode:
    return CodeNode(
        id="code:f", signature="f", name="f", file_path=str(path),
        start_line=1, end_line=end_line, label="FUNCTION",
    )


def _generic_contract() -> dict:
    return {
        "table_access_rules": [{
            "kind": "wrapper_key",
            "id": "generic_key",
            "wrapper": "db_exec",
            "separator": "_",
            "op_tokens": ["r"],
            "suffix_digits": 3,
            "op_mappings": [{"token": "r", "op": "SELECT"}],
        }],
        "ddl_transform": {"ddl_naming_rule": "as_is", "fallback_prefixes": []},
    }


class DeterministicSkillSelectionTest(unittest.IsolatedAsyncioTestCase):
    async def test_partial_upload_selects_registered_skill_from_one_source_match(self) -> None:
        with TemporaryDirectory() as td:
            root = Path(td)
            source = root / "sample.c"
            source.write_text(
                'void f(void) { mpfmdbio("orders_s0001"); }\n',
                encoding="utf-8",
            )
            invoke = AsyncMock(side_effect=AssertionError("registered SKILL called the Agent"))
            with patch("agents.scan_agent._run_agent", new=invoke):
                result = await run_scan_agent(
                    project_root=root,
                    scanner_input={},
                    run_dir=root,
                    cost_budget=CostBudget(limit_usd=1.0),
                    code_nodes=[_node(source, 1)],
                    tables=[],
                )

        invoke.assert_not_awaited()
        self.assertEqual("proframe", result.matched_skill)
        self.assertEqual("completed", result.table_access_status)

    async def test_unique_verified_skill_uses_zero_agent_calls(self) -> None:
        with TemporaryDirectory() as td:
            root = Path(td)
            source = root / "sample.c"
            source.write_text(
                'void f(void) {\n'
                '  mpfmdbio("orders_s0001");\n'
                '  mpfmdbio("users_i0002");\n'
                '  mpfmdbio("audit_u0003");\n'
                '  mpfmdbio("ticket_s_s0001");\n'
                '}\n',
                encoding="utf-8",
            )
            invoke = AsyncMock(side_effect=AssertionError("known SKILL called the Agent"))
            with (
                patch("agents.scan_agent._run_agent", new=invoke),
                patch(
                    "pipeline.stages.links.tables.regex_table_linker.split_source_for_linking",
                    wraps=split_source_for_linking,
                ) as split_source,
            ):
                result = await run_scan_agent(
                    project_root=root,
                    scanner_input={},
                    run_dir=root,
                    cost_budget=CostBudget(limit_usd=1.0),
                    code_nodes=[_node(source)],
                    tables=[
                        Table(db="d", schema="s", name="orders"),
                        Table(db="d", schema="s", name="users"),
                        Table(db="d", schema="s", name="audit"),
                    ],
                )

        invoke.assert_not_awaited()
        self.assertEqual(1, split_source.call_count)
        self.assertEqual("proframe", result.matched_skill)
        self.assertFalse(hasattr(result, "evidence_locations"))
        self.assertEqual(["PFM_CATCH"], result.handler_markers)
        self.assertTrue(result.api_contracts)
        self.assertEqual(1, len(result.table_access_exclusion_rules))

    async def test_no_verified_skill_falls_back_to_unknown_agent(self) -> None:
        with TemporaryDirectory() as td:
            root = Path(td)
            source = root / "sample.c"
            source.write_text("void f(void) {}\n", encoding="utf-8")
            draft = ScanProposal(
                matched_skill=None,
            )
            invoke = AsyncMock(return_value=draft)
            with (
                patch(
                    "agents.scan_agent._iter_skill_contracts",
                    return_value=[
                        ("candidate-a", _generic_contract()),
                        ("candidate-b", _generic_contract()),
                    ],
                ),
                patch("agents.scan_agent._run_agent", new=invoke),
                patch(
                    "pipeline.stages.links.tables.regex_table_linker.split_source_for_linking",
                    wraps=split_source_for_linking,
                ) as split_source,
            ):
                result = await run_scan_agent(
                    project_root=root,
                    scanner_input={},
                    run_dir=root,
                    cost_budget=CostBudget(limit_usd=1.0),
                    code_nodes=[_node(source, 1)],
                    tables=[],
                )

        invoke.assert_awaited_once()
        self.assertEqual(1, split_source.call_count)
        self.assertIsNone(result.matched_skill)
        self.assertEqual("unavailable", result.table_access_status)
        self.assertEqual(
            "model proposed no table access rule", result.no_match_reason,
        )

    async def test_multiple_verified_skills_fail_instead_of_first_match(self) -> None:
        with TemporaryDirectory() as td:
            root = Path(td)
            source = root / "sample.c"
            source.write_text(
                'void f(void) {\n'
                '  db_exec("orders_r001");\n'
                '  db_exec("users_r002");\n'
                '  db_exec("audit_r003");\n'
                '}\n',
                encoding="utf-8",
            )
            contracts = [
                ("candidate-a", _generic_contract()),
                ("candidate-b", _generic_contract()),
            ]
            with (
                patch("agents.scan_agent._iter_skill_contracts", return_value=contracts),
                patch(
                    "pipeline.stages.links.tables.regex_table_linker.split_source_for_linking",
                    wraps=split_source_for_linking,
                ) as split_source,
            ):
                with self.assertRaisesRegex(
                    SkillSelectionAmbiguityError, "candidate-a.*candidate-b",
                ):
                    await run_scan_agent(
                        project_root=root,
                        scanner_input={},
                        run_dir=root,
                        cost_budget=CostBudget(limit_usd=1.0),
                        code_nodes=[_node(source, 5)],
                        tables=[
                            Table(db="d", schema="s", name="orders"),
                            Table(db="d", schema="s", name="users"),
                            Table(db="d", schema="s", name="audit"),
                        ],
                    )
            self.assertEqual(1, split_source.call_count)


if __name__ == "__main__":
    unittest.main()
