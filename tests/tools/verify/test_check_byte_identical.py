from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.verify import check_byte_identical as subject


class CheckByteIdenticalTests(unittest.TestCase):
    def test_missing_baseline_is_read_only_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            baseline = Path(tmp) / "prompt_hashes.json"
            with (
                patch.object(subject, "HASHES_PATH", baseline),
                patch.object(subject, "compute_hashes", return_value={"prompts/a.md": "abc"}),
            ):
                self.assertEqual(2, subject.main())
            self.assertFalse(baseline.exists())

    def test_update_explicitly_writes_baseline(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            baseline = Path(tmp) / "prompt_hashes.json"
            with (
                patch.object(subject, "HASHES_PATH", baseline),
                patch.object(subject, "compute_hashes", return_value={"prompts/a.md": "abc"}),
            ):
                self.assertEqual(0, subject.main(update=True))
            self.assertTrue(baseline.exists())


if __name__ == "__main__":
    unittest.main()
