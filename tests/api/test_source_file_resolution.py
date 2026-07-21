"""Analyzer source endpoint path-resolution contract tests."""

import tempfile
import unittest
from pathlib import Path

from api.source_resolver import AmbiguousSourceFileError, resolve_source_file


class SourceFileResolutionTest(unittest.TestCase):
    def test_unique_suffix_selects_the_intended_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            source_root = Path(temporary_directory)
            expected = source_root / "orders" / "service.c"
            other = source_root / "inventory" / "service.c"
            expected.parent.mkdir()
            other.parent.mkdir()
            expected.write_text("orders", encoding="utf-8")
            other.write_text("inventory", encoding="utf-8")

            self.assertEqual(
                resolve_source_file("orders/service.c", source_root),
                expected.resolve(),
            )

    def test_duplicate_filename_without_path_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            source_root = Path(temporary_directory)
            for directory_name in ("orders", "inventory"):
                source_file = source_root / directory_name / "service.c"
                source_file.parent.mkdir()
                source_file.write_text(directory_name, encoding="utf-8")

            with self.assertRaises(AmbiguousSourceFileError):
                resolve_source_file("service.c", source_root)


if __name__ == "__main__":
    unittest.main()
