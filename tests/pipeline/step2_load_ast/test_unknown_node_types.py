"""미등록 AST 노드 타입의 골키퍼 계약 — 조용한 서브트리 소실 금지.

framework 실측(2026-08-05): `_walk_framework` 는 ontology 에 없는 타입을 만나면
핸들러 없이 건너뛰어 **그 서브트리 전체가 소리 없이 증발**했다(PFM_CATCH 라벨
계열 사고의 상위 원인). 계약: 미등록 타입은 ①`unknown_nodes` 로 표면화하고
②자식으로 재귀해 내부의 아는 사실(호출·statement)을 보존한다.
"""
from pathlib import Path
import unittest

from pipeline.steps.step2_load_ast.framework_reader import _read_framework


def _raw_with_unknown_container():
    # FUNCTION 안에 미등록 타입 "LABEL_BLOCK" 이 FUNCTION_CALL 과 RETURN 을 품는다.
    return {
        "type": "FILE", "fileName": "sample.c", "filePath": "sample.c",
        "packageName": "root",
        "children": [{
            "type": "FUNCTION", "name": "f", "startLine": 1, "endLine": 9,
            "signature": "long f(void)",
            "children": [
                {"type": "LABEL_BLOCK", "name": "PFM_CATCH", "startLine": 4, "endLine": 8,
                 "children": [
                     {"type": "FUNCTION_CALL", "name": "cleanup", "startLine": 5, "endLine": 5},
                     {"type": "RETURN", "startLine": 6, "endLine": 6, "expression": "RC_ERR"},
                 ]},
            ],
        }],
    }


class UnknownNodeTypeContractTest(unittest.TestCase):
    def _result(self):
        return _read_framework(
            _raw_with_unknown_container(), "c", Path("."), Path("sample.c.json"))

    def test_unknown_type_is_surfaced_not_silent(self):
        result = self._result()
        kinds = {u["type"] for u in result.unknown_nodes}
        self.assertEqual({"LABEL_BLOCK"}, kinds)

    def test_children_of_unknown_node_are_preserved(self):
        result = self._result()
        # 호출은 소유 routine 으로 귀속되어 살아남아야 한다.
        self.assertEqual(["cleanup"], [c["call_text"] for c in result.call_sites])
        # statement 도 CodeNode 로 보존되어야 한다.
        self.assertIn("RETURN", {n.label for n in result.code_nodes})


if __name__ == "__main__":
    unittest.main()
