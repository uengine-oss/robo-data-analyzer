from __future__ import annotations

import importlib
import unittest
from pathlib import Path


class ExternalDependencyContractTest(unittest.TestCase):
    def test_direct_runtime_dependencies_import_and_are_declared(self) -> None:
        for module in (
            "charset_normalizer", "regex", "json_source_map", "markdown_it",
        ):
            with self.subTest(module=module):
                self.assertIsNotNone(importlib.import_module(module))
        requirements = (Path(__file__).resolve().parents[3] / "requirements.txt").read_text(encoding="utf-8")
        normalized = requirements.lower().replace("_", "-")
        for package in (
            "charset-normalizer", "regex", "json-source-map", "markdown-it-py",
        ):
            self.assertIn(package, normalized)
