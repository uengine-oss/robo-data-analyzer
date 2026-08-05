# -*- coding: utf-8 -*-
"""분기표 감사 산출물은 활성 분석 run만 소유한다."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from graph.schema.graph_nodes import CodeNode
from pipeline.execution.run_output_context import reset_run_dir, set_run_dir
from pipeline.quality_gates.branch_inventory import build_inventory
from pipeline.steps.step7_analyze_code.three_split_llm_fanout import _persist_branch_table


def _node() -> CodeNode:
    return CodeNode(
        id="code:sample.fn", name="fn", signature="fn()", file_path="sample.c",
        start_line=1, end_line=1, label="FUNCTION",
    )


class BranchTableRunOwnership(unittest.TestCase):
    def test_without_active_run_no_directory_is_created(self) -> None:
        with patch("pathlib.Path.mkdir") as mkdir:
            _persist_branch_table(_node(), build_inventory("return 0;", (), "c", statements=()))
        mkdir.assert_not_called()

    def test_active_run_receives_branch_table(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            token = set_run_dir(root)
            try:
                _persist_branch_table(_node(), build_inventory("return 0;", (), "c", statements=()))
            finally:
                reset_run_dir(token)
            output = root / "branch-tables" / "code_sample.fn.json"
            self.assertTrue(output.is_file())
            self.assertIsInstance(json.loads(output.read_text(encoding="utf-8")), list)

    def test_active_run_write_failure_is_not_downgraded_to_success(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            token = set_run_dir(Path(temp))
            try:
                with (
                    patch("pathlib.Path.write_text", side_effect=PermissionError("denied")),
                    self.assertRaisesRegex(PermissionError, "denied"),
                ):
                    _persist_branch_table(
                        _node(), build_inventory("return 0;", (), "c", statements=())
                    )
            finally:
                reset_run_dir(token)


if __name__ == "__main__":
    unittest.main()
