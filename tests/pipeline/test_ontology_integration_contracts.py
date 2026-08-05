from __future__ import annotations

import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pipeline import languages
from pipeline.steps.step3_load_external.parsers import key_roles
from pipeline.steps.step4_scan_project.scanner_input_collector import (
    collect_scanner_input,
)
from pipeline.steps.step5_linking.link_external import resolver_catalog


_CACHED_POLICIES = (
    languages.external_resolvers,
    languages.external_addressable_attributes,
    languages.external_addressable_child_text,
    languages.external_addressable_kinds,
    languages.scanner_hints,
    languages.external_file_categories,
)


class OntologyIntegrationContractsTest(unittest.TestCase):
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
            resolver_catalog, "external_resolvers", return_value={"customRead": (2,)}
        ):
            self.assertEqual({"customRead": (2,)}, resolver_catalog.promote_wrappers([]))

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

    def test_scanner_hints_are_data_driven_and_manifest_decode_is_strict(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            (root / "custom.manifest").write_text("name=value", encoding="utf-8")
            (root / "sample.zz").write_text("content", encoding="utf-8")
            with patch(
                "pipeline.steps.step4_scan_project.scanner_input_collector.scanner_hints",
                return_value=(("custom.manifest",), {"Custom": frozenset({".zz"})}),
            ):
                result = collect_scanner_input(root)
            self.assertEqual("name=value", result["manifest_content"])
            self.assertEqual(["sample.zz"], result["scanner_categories"]["Custom"])

            (root / "custom.manifest").write_bytes(b"\xff\xfe")
            with (
                patch(
                    "pipeline.steps.step4_scan_project.scanner_input_collector.scanner_hints",
                    return_value=(("custom.manifest",), {"Custom": frozenset({".zz"})}),
                ),
                self.assertRaises(UnicodeDecodeError),
            ):
                collect_scanner_input(root)


if __name__ == "__main__":
    unittest.main()
