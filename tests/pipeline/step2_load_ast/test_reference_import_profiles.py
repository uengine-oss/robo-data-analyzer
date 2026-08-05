from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from graph.schema.graph_nodes import CodeNode, Constant
from graph.schema.node_ids import child_id, code_ns_id
from pipeline.steps.step2_load_ast.import_scopes import _build_scopes
from pipeline.steps.step2_load_ast.token_matcher import _pass2_hits, link_references


class ImportProfileTest(unittest.TestCase):
    def test_local_file_profile_exposes_only_imported_file_names(self) -> None:
        targets = {
            "header": {"LIMIT": [("header:LIMIT", "CONSTANT")]},
            "other": {"OTHER": [("other:OTHER", "CONSTANT")]},
        }
        scope = _build_scopes(
            [{"name": '"common.h"', "file": "main.c"}],
            targets, {}, {"common.h": {"header"}, "other.h": {"other"}},
        )["main.c"]
        self.assertEqual(
            [("header:LIMIT", "CONSTANT")],
            _pass2_hits("use(LIMIT, OTHER)", scope, targets, set()),
        )

    def test_namespace_profile_supports_qualified_and_direct_member_imports(self) -> None:
        module_id = child_id(code_ns_id("pkg"), "Constants")
        targets = {module_id: {"MAX": [("constant:max", "CONSTANT")]}}
        scope = _build_scopes(
            [
                {"name": "pkg.Constants", "file": "Order.java"},
                {"name": "static pkg.Constants.MAX", "file": "Order.java"},
            ],
            targets, {}, {},
        )["Order.java"]
        self.assertEqual(
            [("constant:max", "CONSTANT")],
            _pass2_hits("Constants.MAX + MAX", scope, targets, set()),
        )

    def test_source_module_profile_supports_from_import(self) -> None:
        targets = {"module:config": {"LIMIT": [("config:LIMIT", "CONSTANT")]}}
        scope = _build_scopes(
            [{"name": "from pkg.config import LIMIT", "file": "pkg/use.py"}],
            targets, {"pkg/config.py": {"module:config"}}, {},
        )["pkg/use.py"]
        self.assertEqual(
            [("config:LIMIT", "CONSTANT")],
            _pass2_hits("return LIMIT", scope, targets, set()),
        )

    def test_source_module_profile_supports_relative_from_import(self) -> None:
        targets = {"module:config": {"LIMIT": [("config:LIMIT", "CONSTANT")]}}
        scope = _build_scopes(
            [{"name": "from .config import LIMIT", "file": "pkg/use.py"}],
            targets, {"pkg/config.py": {"module:config"}}, {},
        )["pkg/use.py"]
        self.assertEqual(
            [("config:LIMIT", "CONSTANT")],
            _pass2_hits("return LIMIT", scope, targets, set()),
        )

    def test_unknown_resolver_fails_instead_of_degrading_to_same_file(self) -> None:
        with patch(
            "pipeline.steps.step2_load_ast.import_scopes.reference_import_profile",
            return_value={"resolver": "unknown"},
        ):
            with self.assertRaisesRegex(ValueError, "지원하지 않는 reference import resolver"):
                _build_scopes(
                    [{"name": '"common.h"', "file": "main.c"}], {}, {}, {},
                )


class ReferenceLexingContractTest(unittest.IsolatedAsyncioTestCase):
    async def test_multiline_comment_and_string_do_not_create_references(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "sample.c"
            source.write_text(
                "void f(void) {\n"
                "  /*\n"
                "   LIMIT\n"
                "  */\n"
                "  log(\"LIMIT\");\n"
                "}\n",
                encoding="utf-8",
            )
            refs, _ = await link_references(
                code_nodes=[CodeNode(
                    id="fn", signature="f()", file_path=str(source),
                    start_line=1, end_line=6, label="FUNCTION", owner_id="module",
                )],
                variables=[],
                constants=[Constant(
                    id="limit", owner_id="module", name="LIMIT", file_path=str(source),
                    start_line=1, end_line=1, label="CONSTANT_FIELD", value="1",
                )],
                import_refs=[],
            )
        self.assertEqual([], refs)

    async def test_identifier_pattern_comes_from_language_lexicon(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "Sample.java"
            source.write_text("void f() { use($LIMIT); }\n", encoding="utf-8")
            refs, _ = await link_references(
                code_nodes=[CodeNode(
                    id="fn", signature="f()", file_path=str(source),
                    start_line=1, end_line=1, label="METHOD", owner_id="module",
                )],
                variables=[],
                constants=[Constant(
                    id="limit", owner_id="module", name="$LIMIT", file_path=str(source),
                    start_line=1, end_line=1, label="CONSTANT_FIELD", value="1",
                )],
                import_refs=[],
            )
        self.assertEqual([("fn", "limit")], [
            (edge.source_id, edge.target_id) for edge in refs
        ])


if __name__ == "__main__":
    unittest.main()
