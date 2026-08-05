from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from agents import call_review
from graph.schema.graph_edges import Calls
from graph.schema.graph_nodes import CodeNode
from llm.usage.cost_budget import CostBudget
from agents.contracts import DdlTransform, TableAccessPattern
from pipeline.steps.step4_scan_project.scan_result_contract import (
    ScanResultContractError,
    require_scan_result,
)
from pipeline.steps.step5_linking.link_calls.link_calls_by_name_step import (
    _maybe_review_externals,
)
from pipeline.steps.step5_linking.link_tables.regex_table_linker import (
    RegexTableLinker,
    _compile_patterns,
)


class ScanResultContractTest(unittest.TestCase):
    def test_missing_scan_result_is_rejected(self) -> None:
        with self.assertRaisesRegex(ScanResultContractError, "missing"):
            require_scan_result({})

    def test_invalid_scan_result_is_rejected(self) -> None:
        with self.assertRaisesRegex(ScanResultContractError, "violates"):
            require_scan_result({"scan_result": {"confidence": "invalid"}})


class ExternalCallReviewFailureTest(unittest.IsolatedAsyncioTestCase):
    async def test_review_failure_is_not_converted_to_kept_edges(self) -> None:
        edge = Calls(caller_id="caller", callee_id="external_fn", scope="external")
        cfg = {
            "run": SimpleNamespace(run_dir=Path(".")),
            "cost_budget": CostBudget(limit_usd=1.0),
        }
        settings = SimpleNamespace(
            analysis_agents=SimpleNamespace(call_review_enabled=True)
        )
        with (
            patch("shared.config.settings.SETTINGS", settings),
            patch.object(
                call_review, "review_external_calls",
                new=AsyncMock(side_effect=RuntimeError("review failed")),
            ),
        ):
            with self.assertRaisesRegex(RuntimeError, "review failed"):
                await _maybe_review_externals([edge], [], cfg)

    async def test_enabled_review_requires_runtime_contract(self) -> None:
        edge = Calls(caller_id="caller", callee_id="external_fn", scope="external")
        settings = SimpleNamespace(
            analysis_agents=SimpleNamespace(call_review_enabled=True)
        )
        with patch("shared.config.settings.SETTINGS", settings):
            with self.assertRaisesRegex(RuntimeError, "requires run and cost_budget"):
                await _maybe_review_externals([edge], [], {})


class RegexTableLinkerFailureTest(unittest.IsolatedAsyncioTestCase):
    def test_invalid_pattern_is_rejected_instead_of_skipped(self) -> None:
        pattern = TableAccessPattern(id="broken", call_regex="(", op_mappings=[])
        with self.assertRaisesRegex(ValueError, "broken"):
            _compile_patterns([pattern])

    async def test_code_source_decode_failure_is_fatal(self) -> None:
        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as temporary_directory:
            source = Path(temporary_directory) / "broken.c"
            source.write_bytes(b"\xff\xfe")
            node = CodeNode(
                id="code:broken", signature="broken", file_path=str(source),
                start_line=1, end_line=1, label="FUNCTION", name="broken",
            )
            pattern = TableAccessPattern(
                id="read", call_regex=r"(?P<entity>table)", op_mappings=[]
            )
            with self.assertRaisesRegex(RuntimeError, "source read failed"):
                await RegexTableLinker().link(
                    project_root=Path(temporary_directory),
                    code_nodes=[node], tables=[], patterns=[pattern],
                    ddl_transform=DdlTransform(ddl_naming_rule="as_is"),
                )

    async def test_files_without_code_nodes_are_not_scanned(self) -> None:
        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            source = root / "valid.c"
            source.write_text("void valid(void) {}\n", encoding="utf-8")
            (root / "unowned.bin").write_bytes(b"\xff\xfe")
            node = CodeNode(
                id="code:valid", signature="valid", file_path=str(source),
                start_line=1, end_line=1, label="FUNCTION", name="valid",
            )
            pattern = TableAccessPattern(
                id="read", call_regex=r"(?P<entity>table)", op_mappings=[]
            )
            outcome = await RegexTableLinker().link(
                project_root=root,
                code_nodes=[node], tables=[], patterns=[pattern],
                ddl_transform=DdlTransform(ddl_naming_rule="as_is"),
            )
        self.assertEqual([], outcome.edges)


if __name__ == "__main__":
    unittest.main()
