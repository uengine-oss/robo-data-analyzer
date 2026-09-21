from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from graph.schema.graph_nodes import CodeNode, Constant, Module
from graph.schema.node_ids import code_file_id
from pipeline.parsing.adapters.import_binding import (
    build_import_binding_environment,
    build_import_resolution_ledger,
)
from pipeline.parsing.resolution.reference_links import link_references


class ImportResolutionLedgerTest(unittest.TestCase):
    @staticmethod
    def _ref(
        *, import_id: str = "a" * 64, evidence_version: str = "1.1.0",
        presence_status: str = "active", evidence_status: str = "complete",
        import_kind: str = "source_file", target_kind: str = "quoted",
        entries: list[dict] | None = None, source_id: str = "main.c",
        target_path: str = "common.h",
    ) -> dict:
        raw_target = (
            f'"{target_path}"' if target_kind == "quoted"
            else f"<{target_path}>"
        )
        binding = {
            "entry_ordinal": 0, "import_kind": import_kind,
            "target_kind": target_kind, "target": raw_target,
            "target_range": {"charOffset": 9, "charLength": len(raw_target)},
            "path_components": [target_path],
            "path_component_ranges": [{
                "charOffset": 10, "charLength": len(target_path),
            }],
            "member": None, "member_range": None,
            "alias": None, "alias_range": None,
            "relative_level": 0, "wildcard": False,
            "locality": "local",
        }
        return {
            "import_id": import_id, "evidence_version": evidence_version,
            "legacy_name": "preview only", "name": "preview only",
            "source_id": source_id, "line": 1,
            "end_line": 1, "range": {"charOffset": 0, "charLength": 80},
            "directive_kind": "include", "entries": entries or [binding],
            "grammar_rule": "includeLine", "presence_status": presence_status,
            "evidence_status": evidence_status,
        }

    @staticmethod
    def _environment(
        *source_ids: str, c_include_roots: tuple[str, ...] = (),
    ):
        return build_import_binding_environment(
            [{"source_id": source_id, "language": "c"} for source_id in source_ids],
            [Module(
                id=code_file_id(source_id), name=Path(source_id).name,
                file_path=source_id, label="FILE",
            ) for source_id in source_ids],
            c_include_roots=c_include_roots,
        )

    def test_internal_filename_candidate_does_not_replace_missing_binding_environment(self) -> None:
        ledger = build_import_resolution_ledger([self._ref()])

        self.assertEqual(1, len(ledger))
        self.assertEqual("unresolved", ledger[0]["outcome"])
        self.assertEqual(
            "insufficient_import_binding_environment", ledger[0]["reason"],
        )
        self.assertEqual([], ledger[0]["internal_module_ids"])

    def test_exact_quoted_local_header_is_internal(self) -> None:
        ledger = build_import_resolution_ledger(
            [self._ref(source_id="src/main.c")],
            environment=self._environment("src/main.c", "src/common.h"),
        )

        self.assertEqual("internal", ledger[0]["outcome"])
        self.assertEqual(
            [code_file_id("src/common.h")], ledger[0]["internal_module_ids"],
        )
        self.assertEqual("", ledger[0]["reason"])

    def test_exact_ordered_include_root_header_is_internal(self) -> None:
        ledger = build_import_resolution_ledger(
            [self._ref(target_kind="angle")],
            environment=self._environment(
                "main.c", "include/common.h", c_include_roots=("include",),
            ),
        )

        self.assertEqual("internal", ledger[0]["outcome"])
        self.assertEqual(
            [code_file_id("include/common.h")], ledger[0]["internal_module_ids"],
        )

    def test_suffix_candidate_without_search_path_stays_unresolved(self) -> None:
        ledger = build_import_resolution_ledger(
            [self._ref(target_kind="angle")],
            environment=self._environment("main.c", "vendor/common.h"),
        )

        self.assertEqual("unresolved", ledger[0]["outcome"])
        self.assertEqual(
            "insufficient_c_include_search_environment", ledger[0]["reason"],
        )

    def test_target_absent_from_selected_closure_is_external(self) -> None:
        ledger = build_import_resolution_ledger(
            [self._ref(target_kind="angle")],
            environment=self._environment("main.c"),
        )

        self.assertEqual("external", ledger[0]["outcome"])
        self.assertEqual(
            "target_outside_selected_source_closure", ledger[0]["reason"],
        )
        self.assertEqual([], ledger[0]["internal_module_ids"])

    def test_parent_path_target_is_invalid_not_normalized(self) -> None:
        ledger = build_import_resolution_ledger(
            [self._ref(target_kind="angle", target_path="../common.h")],
            environment=self._environment("main.c", "common.h"),
        )

        self.assertEqual("unresolved", ledger[0]["outcome"])
        self.assertEqual("invalid_c_import_target", ledger[0]["reason"])

    def test_selected_header_without_file_module_stays_unresolved(self) -> None:
        environment = build_import_binding_environment(
            [
                {"source_id": "src/main.c", "language": "c"},
                {"source_id": "src/common.h", "language": "c"},
            ],
            [Module(
                id=code_file_id("src/main.c"), name="main.c",
                file_path="src/main.c", label="FILE",
            )],
        )
        ledger = build_import_resolution_ledger(
            [self._ref(source_id="src/main.c")], environment=environment,
        )

        self.assertEqual("unresolved", ledger[0]["outcome"])
        self.assertEqual("invalid_c_file_module_projection", ledger[0]["reason"])

    def test_computed_target_stays_unresolved_with_complete_environment(self) -> None:
        ledger = build_import_resolution_ledger(
            [self._ref(import_kind="computed", target_kind="computed")],
            environment=self._environment("main.c"),
        )

        self.assertEqual("unresolved", ledger[0]["outcome"])
        self.assertEqual(
            "insufficient_computed_import_target", ledger[0]["reason"],
        )

    def test_java_type_import_binds_exact_selected_module(self) -> None:
        environment = build_import_binding_environment(
            [
                {"source_id": "app/Sample.java", "language": "java"},
                {"source_id": "data/Repository.java", "language": "java"},
            ],
            [Module(
                id="code:sample.data:Repository", name="Repository",
                file_path="data/Repository.java", label="CLASS",
            )],
        )
        entry = {
            "entry_ordinal": 0, "import_kind": "type",
            "target_kind": "qualified", "target": "sample.data.Repository",
            "target_range": {"charOffset": 7, "charLength": 22},
            "path_components": ["sample", "data", "Repository"],
            "path_component_ranges": [
                {"charOffset": 7, "charLength": 6},
                {"charOffset": 14, "charLength": 4},
                {"charOffset": 19, "charLength": 10},
            ],
            "member": None, "member_range": None, "alias": None,
            "alias_range": None, "relative_level": 0, "wildcard": False,
            "locality": "unspecified",
        }
        ref = self._ref(
            source_id="app/Sample.java", import_kind="type",
            target_kind="qualified", entries=[entry],
        )
        ref["directive_kind"] = "import"

        ledger = build_import_resolution_ledger([ref], environment=environment)

        self.assertEqual("internal", ledger[0]["outcome"])
        self.assertEqual(
            ["code:sample.data:Repository"], ledger[0]["internal_module_ids"],
        )

    def test_inactive_import_is_excluded_not_used_as_scope(self) -> None:
        ledger = build_import_resolution_ledger([
            self._ref(presence_status="inactive"),
        ])

        self.assertEqual("excluded", ledger[0]["outcome"])
        self.assertEqual("inactive_import", ledger[0]["reason"])

    def test_legacy_import_evidence_stays_explicitly_unresolved(self) -> None:
        ledger = build_import_resolution_ledger([
            self._ref(evidence_version="1.0.0"),
        ])

        self.assertEqual("unresolved", ledger[0]["outcome"])
        self.assertEqual(
            "insufficient_structured_import_evidence", ledger[0]["reason"],
        )

    def test_v12_envelope_preserves_the_v11_structured_import_contract(self) -> None:
        ledger = build_import_resolution_ledger([
            self._ref(evidence_version="1.2.0"),
        ])

        self.assertEqual(1, len(ledger))
        self.assertEqual("1.2.0", ledger[0]["evidence_version"])
        self.assertEqual("unresolved", ledger[0]["outcome"])
        self.assertEqual(
            "insufficient_import_binding_environment", ledger[0]["reason"],
        )

    def test_v20_envelope_preserves_structural_import_without_claiming_binding(self) -> None:
        ledger = build_import_resolution_ledger([
            self._ref(evidence_version="2.0.0"),
        ])

        self.assertEqual(1, len(ledger))
        self.assertEqual("2.0.0", ledger[0]["evidence_version"])
        self.assertEqual("unresolved", ledger[0]["outcome"])
        self.assertEqual(
            "insufficient_import_binding_environment", ledger[0]["reason"],
        )

    def test_v21_envelope_preserves_structural_import_without_claiming_binding(self) -> None:
        ledger = build_import_resolution_ledger([
            self._ref(evidence_version="2.1.0"),
        ])

        self.assertEqual(1, len(ledger))
        self.assertEqual("2.1.0", ledger[0]["evidence_version"])
        self.assertEqual("unresolved", ledger[0]["outcome"])
        self.assertEqual(
            "insufficient_import_binding_environment", ledger[0]["reason"],
        )

    def test_every_binding_entry_has_one_source_ordered_outcome(self) -> None:
        first = self._ref()["entries"][0]
        second = {**first, "entry_ordinal": 1, "target": '"other.h"'}
        ledger = build_import_resolution_ledger([
            self._ref(entries=[first, second]),
        ])

        self.assertEqual([0, 1], [row["ordinal"] for row in ledger])
        self.assertEqual([0, 1], [row["entry_ordinal"] for row in ledger])
        self.assertEqual(2, len({row["occurrence_id"] for row in ledger}))

    def test_duplicate_canonical_import_fact_fails_closed(self) -> None:
        with self.assertRaisesRegex(ValueError, "duplicate canonical import"):
            build_import_resolution_ledger([self._ref(), self._ref()])


class ParserReferenceContractTest(unittest.IsolatedAsyncioTestCase):
    async def test_empty_parser_occurrences_create_no_references(self) -> None:
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
            )
        self.assertEqual([], refs)

    async def test_parser_occurrence_is_bound_without_reading_source(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "Sample.java"
            source.write_text("void f() { use($LIMIT); }\n", encoding="utf-8")
            source.unlink()
            refs, _ = await link_references(
                code_nodes=[CodeNode(
                    id="fn", signature="f()", file_path=str(source),
                    start_line=1, end_line=1, label="METHOD", owner_id="module",
                    extra={"_identifier_references": (("$LIMIT", 1),)},
                )],
                variables=[],
                constants=[Constant(
                    id="limit", owner_id="module", name="$LIMIT", file_path=str(source),
                    start_line=1, end_line=1, label="CONSTANT_FIELD", value="1",
                )],
            )
        self.assertEqual([("fn", "limit")], [
            (edge.source_id, edge.target_id) for edge in refs
        ])


if __name__ == "__main__":
    unittest.main()
