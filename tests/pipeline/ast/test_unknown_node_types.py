"""미등록 AST 노드 타입의 골키퍼 계약 — 조용한 서브트리 소실 금지.

framework 실측(2026-08-05): `_walk_framework` 는 ontology 에 없는 타입을 만나면
핸들러 없이 건너뛰어 **그 서브트리 전체가 소리 없이 증발**했다(PFM_CATCH 라벨
계열 사고의 상위 원인). 계약: 미등록 타입은 ①`unknown_nodes` 로 표면화하고
②자식으로 재귀해 내부의 아는 사실(호출·statement)을 보존한다.
"""
from pathlib import Path
import json
import tempfile
import unittest

from pipeline.stages.ast.ast_dispatch import read_antlr_json
from pipeline.stages.ast.framework_reader import _read_framework
from tests.pipeline.parsing.facts.evidence_fixture import add_common_evidence
from tests.pipeline.parser_reference_fixtures import with_identifier_contract


def _raw_with_unknown_container():
    # FUNCTION 안에 미등록 타입 "LABEL_BLOCK" 이 FUNCTION_CALL 과 RETURN 을 품는다.
    return with_identifier_contract({
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
    })


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

    def test_unknown_container_cannot_be_published_as_complete_ast(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "sample.c"
            source_text = (
                "void f(void) {\n\n\n\ncleanup();\nreturn;\n}\n"
            )
            source.write_bytes(source_text.encode("utf-8"))
            artifact = root / "sample.json"
            raw = add_common_evidence(
                _raw_with_unknown_container(), source_text,
                calls=[{
                    "expression": "cleanup()", "callee": "cleanup",
                    "terminal_name": "cleanup",
                }],
            )
            artifact.write_text(json.dumps(raw), encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "LABEL_BLOCK"):
                read_antlr_json(artifact, root)

    def test_malformed_ast_artifact_is_fatal_not_empty_parse_result(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            artifact = root / "sample.json"
            artifact.write_text("{not-json", encoding="utf-8")

            with self.assertRaises((ValueError, json.JSONDecodeError)):
                read_antlr_json(artifact, root)

    def test_initializer_calls_are_preserved_for_evidence_population(self):
        raw = with_identifier_contract({
            "type": "FILE", "fileName": "sample.py", "filePath": "sample.py",
            "packageName": "root",
            "children": [
                {
                    "type": "CONSTANT_FIELD", "name": "PATTERN",
                    "startLine": 1, "endLine": 1,
                    "children": [{
                        "type": "FUNCTION_CALL", "name": "compile",
                        "startLine": 1, "endLine": 1,
                    }],
                },
                {
                    "type": "VARIABLE", "name": "cleaned",
                    "startLine": 2, "endLine": 2,
                    "children": [{
                        "type": "FUNCTION_CALL", "name": "sub",
                        "startLine": 2, "endLine": 2,
                    }],
                },
            ],
        })

        result = _read_framework(raw, "python", Path("."), Path("sample.py.json"))

        self.assertEqual(["compile", "sub"], [call["call_text"] for call in result.call_sites])

    def test_java_constructor_evidence_does_not_require_legacy_new_instance_node(self):
        source = 'class Sample { void run() { throw new Problem("x"); } }\n'
        raw = add_common_evidence(
            with_identifier_contract({
                "type": "FILE", "fileName": "Sample.java", "filePath": "Sample.java",
                "packageName": "root", "children": [],
            }),
            source,
            calls=[{
                "expression": 'new Problem("x")', "callee": "Problem",
                "callee_kind": "constructor", "terminal_name": "Problem",
                "arguments": ['"x"'],
            }],
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "Sample.java").write_text(source, encoding="utf-8")
            artifact = root / "Sample.json"
            artifact.write_text(json.dumps(raw), encoding="utf-8")

            result = read_antlr_json(artifact, root)

        self.assertEqual(1, len(result.call_sites))
        self.assertEqual("constructor", result.call_sites[0]["callee_kind"])
        self.assertEqual("Problem", result.call_sites[0]["call_text"])


if __name__ == "__main__":
    unittest.main()
