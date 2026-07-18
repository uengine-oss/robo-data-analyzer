from __future__ import annotations

import unittest
from pathlib import Path

from pipeline.steps.step2_load_ast.framework_reader import _read_framework


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
                        {"type": "CASE", "startLine": 6, "endLine": 7, "children": []},
                        {"type": "CASE", "startLine": 9, "endLine": 10, "children": []},
                        {"type": "FUNCTION_CALL", "name": "work", "startLine": 11},
                        {"type": "CASE", "startLine": 12, "endLine": 13, "children": []},
                        {"type": "FUNCTION_CALL", "name": "more_work", "startLine": 14},
                        {"type": "IF", "startLine": 15, "endLine": 16, "children": []},
                        {"type": "CASE", "startLine": 18, "endLine": 20, "children": [{
                            "type": "CASE", "startLine": 19, "endLine": 20,
                            "children": [{"type": "FUNCTION_CALL", "name": "fallback", "startLine": 19}],
                        }]},
                    ],
                }],
            }],
        }
        return _read_framework(raw, "c", Path("."), Path("sample.c.json"))

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


if __name__ == "__main__":
    unittest.main()
