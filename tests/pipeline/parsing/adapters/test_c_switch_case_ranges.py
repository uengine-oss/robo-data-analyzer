from __future__ import annotations

import unittest
from copy import deepcopy
from pathlib import Path

from pipeline.stages.ast.framework_reader import _read_framework
from tests.pipeline.parser_reference_fixtures import with_identifier_contract


class CSwitchCaseRangeTests(unittest.TestCase):
    def _read(self):
        raw = {
            "type": "FILE", "fileName": "sample.c", "filePath": "sample.c",
            "children": [{
                "type": "FUNCTION", "name": "dispatch", "startLine": 1,
                "endLine": 25, "signature": "void dispatch(int code)",
                "children": [{
                    "type": "SWITCH", "startLine": 5, "endLine": 20,
                    "children": [
                        {"type": "CASE", "startLine": 6, "endLine": 8, "children": []},
                        {"type": "CASE", "startLine": 9, "endLine": 11, "children": [
                            {"type": "FUNCTION_CALL", "name": "work", "startLine": 11},
                        ]},
                        {"type": "CASE", "startLine": 12, "endLine": 17, "children": [
                            {"type": "FUNCTION_CALL", "name": "more_work", "startLine": 14},
                            {"type": "IF", "startLine": 15, "endLine": 16, "children": []},
                        ]},
                        {"type": "CASE", "startLine": 18, "endLine": 18, "children": []},
                        {"type": "CASE", "startLine": 19, "endLine": 20, "children": [
                            {"type": "FUNCTION_CALL", "name": "fallback", "startLine": 19},
                        ]},
                    ],
                }],
            }],
        }
        raw = with_identifier_contract(raw)
        before = deepcopy(raw)
        result = _read_framework(raw, "c", Path("."), Path("sample.c.json"))
        self.assertEqual(before, raw, "analyzer reader가 parser AST를 재작성하면 안 된다")
        return result

    def test_cases_extend_to_next_label_and_stacked_labels_are_siblings(self) -> None:
        result = self._read()
        switch = next(node for node in result.code_nodes if node.label == "SWITCH")
        cases = sorted(
            (node for node in result.code_nodes if node.label == "CASE"),
            key=lambda node: node.start_line,
        )
        self.assertEqual([(6, 8), (9, 11), (12, 17), (18, 18), (19, 20)],
                         [(node.start_line, node.end_line) for node in cases])
        parents = {(edge.parent_id, edge.child_id) for edge in result.parent_of_edges}
        self.assertTrue(all((switch.id, case.id) in parents for case in cases))
        nested_if = next(node for node in result.code_nodes if node.label == "IF")
        owning_case = next(node for node in cases if node.start_line == 12)
        self.assertIn((owning_case.id, nested_if.id), parents)

    def test_calls_remain_owned_by_the_routine(self) -> None:
        result = self._read()
        routine = next(node for node in result.code_nodes if node.label == "FUNCTION")
        self.assertEqual(
            {"work": routine.id, "more_work": routine.id, "fallback": routine.id},
            {site["call_text"]: site["caller_id"] for site in result.call_sites},
        )

    def test_noncanonical_parser_ownership_is_rejected_not_repaired(self) -> None:
        raw = {
            "type": "FILE", "fileName": "sample.c", "filePath": "sample.c",
            "children": [{
                "type": "FUNCTION", "name": "f", "startLine": 1, "endLine": 8,
                "children": [{
                    "type": "SWITCH", "startLine": 2, "endLine": 7,
                    "children": [
                        {"type": "CASE", "startLine": 3, "endLine": 4, "children": []},
                        {"type": "FUNCTION_CALL", "name": "work", "startLine": 5},
                    ],
                }],
            }],
        }
        with self.assertRaisesRegex(
            ValueError,
            r"sample\.c\.json: SWITCH@2 has FUNCTION_CALL@5 after first CASE",
        ):
            _read_framework(
                with_identifier_contract(raw), "c", Path("."), Path("sample.c.json"),
            )


if __name__ == "__main__":
    unittest.main()
