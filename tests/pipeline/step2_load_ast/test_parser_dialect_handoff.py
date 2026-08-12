from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from pipeline.steps.step2_load_ast.dispatch import read_antlr_json


class ParserDialectHandoffTest(unittest.TestCase):
    def test_dbms_nodes_receive_parser_owned_language_from_diagnostic(self) -> None:
        with TemporaryDirectory() as temp:
            data = Path(temp)
            source = data / "source"
            analysis = data / "analysis"
            diagnostics = data / "diagnostics"
            source.mkdir()
            analysis.mkdir()
            diagnostics.mkdir()
            (source / "sample.sql").write_text("SELECT ID FROM A;\n", encoding="utf-8")
            ast = analysis / "sample.json"
            ast.write_text(json.dumps({
                "type": "FILE",
                "fileName": "sample.sql",
                "filePath": "sample.sql",
                "children": [{
                    "type": "SELECT", "startLine": 1, "endLine": 1, "children": [],
                }],
            }), encoding="utf-8")
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
            (source / "sample.sql").write_text("SELECT ID FROM A;\n", encoding="utf-8")
            ast = analysis / "sample.json"
            ast.write_text(json.dumps({
                "type": "FILE",
                "fileName": "sample.sql",
                "filePath": "sample.sql",
                "children": [{
                    "type": "SELECT", "startLine": 1, "endLine": 1, "children": [],
                }],
            }), encoding="utf-8")

            result = read_antlr_json(ast, source)

            self.assertNotIn("source_language", result.code_nodes[0].extra)


if __name__ == "__main__":
    unittest.main()
