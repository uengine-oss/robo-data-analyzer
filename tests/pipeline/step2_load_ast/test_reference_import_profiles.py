from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from graph.schema.graph_nodes import CodeNode, Constant
from pipeline.steps.step2_load_ast.import_scopes import build_import_scope_plan
from pipeline.steps.step2_load_ast.token_matcher import link_references


class ImportProfileTest(unittest.TestCase):
    @staticmethod
    def _ref(
        *, import_id: str = "a" * 64, evidence_version: str = "1.1.0",
        presence_status: str = "active", evidence_status: str = "complete",
        import_kind: str = "source_file", target_kind: str = "quoted",
        entries: list[dict] | None = None,
    ) -> dict:
        binding = {
            "entry_ordinal": 0, "import_kind": import_kind,
            "target_kind": target_kind, "target": '"common.h"',
            "target_range": {"charOffset": 9, "charLength": 10},
            "path_components": ["common.h"],
            "path_component_ranges": [{"charOffset": 10, "charLength": 8}],
            "member": None, "member_range": None,
            "alias": None, "alias_range": None,
            "relative_level": 0, "wildcard": False,
            "locality": "local",
        }
        return {
            "import_id": import_id, "evidence_version": evidence_version,
            "legacy_name": "preview only", "name": "preview only",
            "source_id": "main.c", "file": "main.c", "line": 1,
            "end_line": 1, "range": {"charOffset": 0, "charLength": 20},
            "directive_kind": "include", "entries": entries or [binding],
            "grammar_rule": "includeLine", "presence_status": presence_status,
            "presence": {"status": presence_status, "provenance": "fixture"},
            "evidence_status": evidence_status,
        }

    def test_internal_filename_candidate_does_not_replace_missing_binding_environment(self) -> None:
        plan = build_import_scope_plan(
            [self._ref()],
        )

        self.assertEqual({}, plan.scopes)
        self.assertEqual(1, len(plan.ledger))
        self.assertEqual("unresolved", plan.ledger[0]["outcome"])
        self.assertEqual(
            "insufficient_import_binding_environment", plan.ledger[0]["reason"],
        )
        self.assertEqual([], plan.ledger[0]["resolved_module_ids"])

    def test_inactive_import_is_excluded_not_used_as_scope(self) -> None:
        plan = build_import_scope_plan(
            [self._ref(presence_status="inactive")],
        )

        self.assertEqual({}, plan.scopes)
        self.assertEqual("excluded", plan.ledger[0]["outcome"])
        self.assertEqual("inactive_import", plan.ledger[0]["reason"])

    def test_legacy_import_evidence_stays_explicitly_unresolved(self) -> None:
        plan = build_import_scope_plan(
            [self._ref(evidence_version="1.0.0")],
        )

        self.assertEqual({}, plan.scopes)
        self.assertEqual("unresolved", plan.ledger[0]["outcome"])
        self.assertEqual(
            "insufficient_structured_import_evidence", plan.ledger[0]["reason"],
        )

    def test_v12_envelope_preserves_the_v11_structured_import_contract(self) -> None:
        plan = build_import_scope_plan(
            [self._ref(evidence_version="1.2.0")],
        )

        self.assertEqual({}, plan.scopes)
        self.assertEqual(1, len(plan.ledger))
        self.assertEqual("1.2.0", plan.ledger[0]["evidence_version"])
        self.assertEqual("unresolved", plan.ledger[0]["outcome"])
        self.assertEqual(
            "insufficient_import_binding_environment", plan.ledger[0]["reason"],
        )

    def test_every_binding_entry_has_one_source_ordered_outcome(self) -> None:
        first = self._ref()["entries"][0]
        second = {**first, "entry_ordinal": 1, "target": '"other.h"'}
        plan = build_import_scope_plan(
            [self._ref(entries=[first, second])],
        )

        self.assertEqual([0, 1], [row["ordinal"] for row in plan.ledger])
        self.assertEqual([0, 1], [row["entry_ordinal"] for row in plan.ledger])
        self.assertEqual(2, len({row["occurrence_id"] for row in plan.ledger}))

    def test_duplicate_canonical_import_fact_fails_closed(self) -> None:
        with self.assertRaisesRegex(ValueError, "duplicate canonical import"):
            build_import_scope_plan([self._ref(), self._ref()])


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
