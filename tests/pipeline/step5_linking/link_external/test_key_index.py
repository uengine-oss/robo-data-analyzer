from __future__ import annotations

import unittest

from pipeline.steps.step5_linking.link_external.key_index import (
    dynamic_key_candidates,
    match_keys,
    match_keys_exhaustive,
)


class KeyIndexTest(unittest.TestCase):
    def test_exact_literals_only_and_comments_are_ignored(self) -> None:
        code = '''
// use("shop.comment")
/* 'shop.block' */
String partial = "shop.direct.more";
use("shop.direct");
use('shop.single');
String escaped = "shop.\\u006bey";
'''
        keys = ["shop.comment", "shop.block", "shop.direct", "shop.single", "shop.key"]
        self.assertEqual(
            [("shop.direct", 5), ("shop.single", 6), ("shop.key", 7)],
            match_keys(code, keys),
        )

    def test_key_index_is_result_equivalent_to_exhaustive_at_scale(self) -> None:
        keys = [f"domain.item.{index}" for index in range(2000)]
        code = '\n'.join(f'use("domain.item.{index}");' for index in (3, 199, 1042, 1999))
        indexed = set(match_keys(code, keys))
        exhaustive = set(match_keys_exhaustive(code, keys))
        self.assertEqual(exhaustive, indexed)

    def test_dynamic_concat_is_suspicion_not_direct_match(self) -> None:
        code = 'use("shop.order." + operation); other = "unrelated" + suffix;'
        keys = ["shop.order.find", "shop.order.save", "different.key"]
        self.assertEqual([], match_keys(code, keys))
        self.assertEqual(["shop.order.find", "shop.order.save"], dynamic_key_candidates(code, keys))


if __name__ == "__main__":
    unittest.main()
