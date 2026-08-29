from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from pipeline.steps.step2_load_ast.dispatch import read_antlr_json
from tests.pipeline.step2_load_ast.evidence_fixture import add_common_evidence


class ParserDialectHandoffTest(unittest.TestCase):
    def test_dbms_call_without_routine_scope_is_preserved_for_exact_once_accounting(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            source_text = "BEGIN work(); END;\n"
            (root / "sample.sql").write_bytes(source_text.encode("utf-8"))
            raw = add_common_evidence({
                "type": "FILE", "fileName": "sample.sql", "filePath": "sample.sql",
                "children": [],
            }, source_text, calls=[{
                "expression": "work()", "callee": "work", "terminal_name": "work",
            }])
            artifact = root / "sample.json"
            artifact.write_text(json.dumps(raw), encoding="utf-8")

            result = read_antlr_json(artifact, root)

            self.assertEqual(1, len(result.call_sites))
            self.assertEqual("", result.call_sites[0]["caller_id"])
            self.assertEqual(
                "unresolved_no_routine", result.call_sites[0]["owner_status"],
            )
            self.assertEqual("work", result.call_sites[0]["call_text"])
            self.assertEqual("work()", result.call_sites[0]["call_source"])

    def test_dbms_nodes_receive_parser_owned_language_from_diagnostic(self) -> None:
        with TemporaryDirectory() as temp:
            data = Path(temp)
            source = data / "source"
            analysis = data / "analysis"
            diagnostics = data / "diagnostics"
            source.mkdir()
            analysis.mkdir()
            diagnostics.mkdir()
            source_text = "SELECT ID FROM A;\n"
            (source / "sample.sql").write_bytes(source_text.encode("utf-8"))
            ast = analysis / "sample.json"
            ast.write_text(json.dumps(add_common_evidence({
                "type": "FILE",
                "fileName": "sample.sql",
                "filePath": "sample.sql",
                "children": [{
                    "type": "SELECT", "startLine": 1, "endLine": 1, "children": [],
                }],
            }, source_text, call_supported=False)), encoding="utf-8")
            (diagnostics / "sample.sql.parse.json").write_text(json.dumps({
                "sourcePath": "sample.sql", "language": "oracle",
            }), encoding="utf-8")

            result = read_antlr_json(ast, source)

            self.assertEqual("oracle", result.code_nodes[0].extra["source_language"])

    def test_missing_diagnostic_leaves_language_unset(self) -> None:
        with TemporaryDirectory() as temp:
            data = Path(temp)
            source = data / "source"
            analysis = data / "analysis"
            source.mkdir()
            analysis.mkdir()
            source_text = "SELECT ID FROM A;\n"
            (source / "sample.sql").write_bytes(source_text.encode("utf-8"))
            ast = analysis / "sample.json"
            ast.write_text(json.dumps(add_common_evidence({
                "type": "FILE",
                "fileName": "sample.sql",
                "filePath": "sample.sql",
                "children": [{
                    "type": "SELECT", "startLine": 1, "endLine": 1, "children": [],
                }],
            }, source_text, call_supported=False)), encoding="utf-8")

            result = read_antlr_json(ast, source)

            self.assertNotIn("source_language", result.code_nodes[0].extra)


if __name__ == "__main__":
    unittest.main()
