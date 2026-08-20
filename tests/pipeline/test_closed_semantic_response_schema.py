from __future__ import annotations

import unittest

from pipeline.languages import require_stereotype


class ClosedSemanticResponseSchemaTest(unittest.TestCase):
    def test_code_stereotype_validation_never_erases_invalid_meaning(self) -> None:
        self.assertEqual("Query", require_stereotype("Query", "framework"))
        for invalid in ("", "DataQuery", "Unknown"):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                require_stereotype(invalid, "framework")

if __name__ == "__main__":
    unittest.main()
