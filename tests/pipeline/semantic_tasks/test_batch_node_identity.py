from __future__ import annotations

import unittest

from pipeline.semantic_tasks.batch_node_identity import batch_node_keys


class BatchNodeIdentityTest(unittest.TestCase):
    def test_keys_are_compact_request_ordinals(self) -> None:
        self.assertEqual({"N1": "a", "N2": "b"}, batch_node_keys(("a", "b")))

    def test_duplicate_internal_identity_is_rejected_without_hash_fallback(self) -> None:
        with self.assertRaises(ValueError):
            batch_node_keys(("same", "same"))


if __name__ == "__main__":
    unittest.main()
