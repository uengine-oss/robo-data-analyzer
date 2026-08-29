from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from pipeline.steps.step4_scan_project.scanner_input_collector import (
    collect_scanner_input,
)


class ScannerInputScopeTest(unittest.TestCase):
    def test_partial_source_scope_keeps_root_manifests_without_counting_other_sources(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            selected = root / "selected.c"
            selected.write_text("int selected(void);\n", encoding="utf-8")
            (root / "unselected.c").write_text(
                "int unselected(void);\n", encoding="utf-8",
            )
            (root / "pom.xml").write_text("<project/>\n", encoding="utf-8")

            scanner_input = collect_scanner_input(
                root,
                selected_source_paths=(selected,),
            )

        self.assertEqual({"c": 1}, scanner_input["extension_counts"])
        self.assertEqual(["pom.xml"], scanner_input["manifest_paths"])


if __name__ == "__main__":
    unittest.main()
