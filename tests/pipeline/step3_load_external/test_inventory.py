from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from pipeline.steps.step3_load_external.inventory import InventorySecurityError, inventory_project
from shared.files.cache import SourceFileCache, SourceFileReadError


class InventoryTest(unittest.TestCase):
    def test_sorted_classification_and_build_exclusion(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "src").mkdir()
            (root / "src" / "A.java").write_text("class A {}", encoding="utf-8")
            (root / "app.properties").write_text("key=값", encoding="cp949")
            (root / "README.md").write_text("# 문서", encoding="utf-8")
            (root / ".env").write_text("KEY=value", encoding="utf-8")
            (root / "mapper.xml").write_text("<mapper/>", encoding="utf-8")
            (root / "blob.bin").write_bytes(b"x\x00y")
            (root / "guide.pdf").write_bytes(b"%PDF-1.7\x00binary")
            (root / "unknown.xyz").write_text("plain", encoding="utf-8")
            (root / "build").mkdir()
            (root / "build" / "generated.json").write_text("{}", encoding="utf-8")

            items = inventory_project(root, SourceFileCache())
            self.assertEqual(sorted(i.relpath for i in items), [i.relpath for i in items])
            by_name = {i.relpath: i for i in items}
            self.assertEqual("source", by_name["src/A.java"].category)
            self.assertEqual("config", by_name["app.properties"].category)
            self.assertEqual("document", by_name["README.md"].category)
            self.assertNotIn(".env", by_name)
            self.assertEqual("artifact_xml", by_name["mapper.xml"].category)
            self.assertEqual(("binary", "unanalyzed"), (by_name["blob.bin"].category, by_name["blob.bin"].status))
            self.assertEqual(("document", "registered"), (by_name["guide.pdf"].category, by_name["guide.pdf"].status))
            self.assertEqual(("unknown", "unsupported_text_format"), (by_name["unknown.xyz"].category, by_name["unknown.xyz"].reason))
            self.assertNotIn("build/generated.json", by_name)

    @unittest.skipUnless(hasattr(os, "symlink"), "symlinks unavailable")
    def test_symlink_file_escape_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp, tempfile.TemporaryDirectory() as outside:
            root = Path(temp)
            target = Path(outside) / "secret.txt"
            target.write_text("secret", encoding="utf-8")
            link = root / "escape.txt"
            try:
                link.symlink_to(target)
            except OSError as error:
                self.skipTest(f"symlink creation unavailable: {error}")
            with self.assertRaises(InventorySecurityError):
                inventory_project(root, SourceFileCache())

    def test_file_read_failure_is_recorded_not_dropped(self) -> None:
        class FailingCache(SourceFileCache):
            def read_bytes(self, file_path):
                raise SourceFileReadError(str(file_path), PermissionError("denied"))

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "locked.json").write_text("{}", encoding="utf-8")
            item = inventory_project(root, FailingCache())[0]
            self.assertEqual(("failed", "read_failed"), (item.status, item.reason))


if __name__ == "__main__":
    unittest.main()
