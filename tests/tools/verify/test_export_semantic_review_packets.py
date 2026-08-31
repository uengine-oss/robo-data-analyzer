from __future__ import annotations

import unittest

from tools.verify.export_semantic_review_packets import _selected_targets


class SemanticReviewTargetSelectionTest(unittest.TestCase):
    def test_no_selection_exports_the_complete_prompt_population(self) -> None:
        self.assertEqual(
            ["code:a", "code:b"],
            _selected_targets({"code:b", "code:a"}, []),
        )

    def test_explicit_selection_is_exact_and_unknown_target_fails(self) -> None:
        self.assertEqual(
            ["code:b"],
            _selected_targets({"code:a", "code:b"}, ["code:b", "code:b"]),
        )
        with self.assertRaisesRegex(ValueError, "no prompt record"):
            _selected_targets({"code:a"}, ["code:missing"])


if __name__ == "__main__":
    unittest.main()
