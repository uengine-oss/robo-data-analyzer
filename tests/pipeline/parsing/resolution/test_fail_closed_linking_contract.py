from pathlib import Path
import unittest

from graph.schema.graph_nodes import CodeNode
from agents.agent_contracts import DdlTransform, OpMapping, TableAccessPattern
from pipeline.stages.scan.scan_result_contract import (
    ScanResultContractError,
    require_scan_result,
)
from pipeline.stages.links.tables.regex_table_linker import (
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
