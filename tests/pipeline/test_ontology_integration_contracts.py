from __future__ import annotations

import copy
import tempfile
import unittest
import json
from pathlib import Path
from pathlib import Path
from unittest.mock import patch

from pipeline import source_languages as languages
from pipeline.stages.external.parsers import key_roles
from pipeline.stages.scan.scanner_input_collector import (
    collect_scanner_input,
)
from pipeline.stages.links.external import external_linker as linker


_CACHED_POLICIES = (
    languages.external_resolvers,
    languages.external_addressable_attributes,
    languages.external_addressable_child_text,
    languages.external_addressable_kinds,
    languages.scanner_hints,
    languages.external_file_categories,
)


class OntologyIntegrationContractsTest(unittest.TestCase):
    def test_dbms_extensions_match_parser_catalog(self) -> None:
        from pipeline.source_languages import DBMS_SUFFIXES

        catalog_path = (
            Path(__file__).resolve().parents[3]
            / "antlr-code-parser"
            / "src"
            / "main"
            / "resources"
            / "languages"
            / "language-catalog.json"
        )
        catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
        parser_extensions = {
            extension
            for language in catalog["languages"]
            if language["family"] == "dbms"
            for extension in language["parseExtensions"]
        }
        self.assertEqual(parser_extensions, set(DBMS_SUFFIXES))

    def tearDown(self) -> None:
        for function in _CACHED_POLICIES:
            function.cache_clear()

    def _ontology(self) -> dict:
        return copy.deepcopy(languages._load_ontology())

    def test_resolver_positions_reject_duplicates_and_invalid_indexes(self) -> None:
        data = self._ontology()
        for invalid in ([0, 0], [-1], [True]):
            data["external_linking"]["resolvers"] = {"lookup": invalid}
            languages.external_resolvers.cache_clear()
            with (
                patch.object(languages, "_load_ontology", return_value=data),
                self.assertRaises(ValueError),
            ):
                languages.external_resolvers()

    def test_scanner_categories_reject_cross_category_extension_overlap(self) -> None:
        data = self._ontology()
        data["scanner_hints"]["categories"] = {
            "First": ["xml"],
            "Second": [".xml"],
        }
        languages.scanner_hints.cache_clear()
        with (
            patch.object(languages, "_load_ontology", return_value=data),
            self.assertRaisesRegex(ValueError, "확장자 중복"),
        ):
            languages.scanner_hints()

    def test_external_file_categories_reject_invalid_and_overlapping_rules(self) -> None:
        invalid_sections = (
            {"config": {"suffixes": [""], "exact_names": []}},
            {"config": {"suffixes": ["java"], "exact_names": []}},
            {
                "config": {"suffixes": ["cfg"], "exact_names": []},
                "document": {"suffixes": [".cfg"], "exact_names": []},
            },
            {"config": {"suffixes": [], "exact_names": ["dir/.env"]}},
        )
        for section in invalid_sections:
            with self.subTest(section=section):
                data = self._ontology()
                data["external_file_categories"] = section
                languages.external_file_categories.cache_clear()
                with (
                    patch.object(languages, "_load_ontology", return_value=data),
                    self.assertRaises(ValueError),
                ):
                    languages.external_file_categories()

    def test_consumers_use_data_owned_resolver_and_address_roles(self) -> None:
        with patch.object(
            linker, "external_resolvers", return_value={"customRead": (2,)}
        ):
            self.assertEqual({"customRead": (2,)}, linker.external_resolvers())

        with (
            patch.object(
                key_roles, "external_addressable_attributes",
                return_value={"custom": frozenset({"key"})},
            ),
            patch.object(
                key_roles, "external_addressable_child_text",
                return_value={"parent": frozenset({"child"})},
            ),
            patch.object(
                key_roles, "external_addressable_kinds",
                return_value=frozenset({"custom_entry"}),
            ),
        ):
            self.assertTrue(key_roles.is_addressable_attribute("ns:custom", "KEY"))
            self.assertTrue(key_roles.is_addressable_child_text("parent", "ns:child"))
            self.assertTrue(key_roles.is_addressable_kind("custom_entry"))

    def test_scanner_hints_are_complete_compact_inventory_not_inline_source(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            manifest = root / "custom.manifest"
            manifest.write_text(
                "\n".join(f"key{line}=value{line}" for line in range(80)),
                encoding="utf-8",
            )
            for index in range(55):
                (root / f"sample-{index}.zz").write_text("content", encoding="utf-8")
            with patch(
                "pipeline.stages.scan.scanner_input_collector.scanner_hints",
                return_value=(("custom.manifest",), {"Custom": frozenset({".zz"})}),
            ):
                result = collect_scanner_input(root)
            self.assertEqual(["custom.manifest"], result["manifest_paths"])
            self.assertNotIn("manifest_content", result)
            self.assertEqual(55, result["scanner_category_file_counts"]["Custom"])
            self.assertEqual({"manifest": 1, "zz": 55}, result["extension_counts"])

    def test_scanner_inventory_keeps_every_extension_kind(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            for index in range(25):
                (root / f"source.x{index:02d}").write_text("content", encoding="utf-8")
            with patch(
                "pipeline.stages.scan.scanner_input_collector.scanner_hints",
                return_value=((), {}),
            ):
                result = collect_scanner_input(root)

        self.assertEqual(25, len(result["extension_counts"]))

    def test_scanner_inventory_counts_only_explicit_partial_upload_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            selected = root / "selected.zz"
            outside = root / "outside.yy"
            selected.write_text("selected", encoding="utf-8")
            outside.write_text("outside", encoding="utf-8")
            with patch(
                "pipeline.stages.scan.scanner_input_collector.scanner_hints",
                return_value=((), {
                    "Selected": frozenset({".zz"}),
                    "Outside": frozenset({".yy"}),
                }),
            ):
                result = collect_scanner_input(
                    root,
                    selected_source_paths=(selected,),
                )

        self.assertEqual({"zz": 1}, result["extension_counts"])
        self.assertEqual(1, result["scanner_category_file_counts"]["Selected"])
        self.assertEqual(0, result["scanner_category_file_counts"]["Outside"])


if __name__ == "__main__":
    unittest.main()
