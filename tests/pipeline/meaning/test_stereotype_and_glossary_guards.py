# -*- coding: utf-8 -*-
"""spec 116 — stereotype 결정론 배제와 ontology 우선순위 계약."""
from __future__ import annotations

import unittest

from pipeline.source_languages import (
    exclude_contradicted_stereotype,
    stereotype_priority_text,
)


class StereotypeRules(unittest.TestCase):
    def test_write_evidence_forbids_query_family(self):
        for name in ("Query", "DataQuery", "MetadataLookup"):
            with self.subTest(stereotype=name):
                value, violation = exclude_contradicted_stereotype(
                    name, has_writes=True
                )
                self.assertEqual("Unclassified", value)
                self.assertIn("모순", violation)

    def test_no_writes_keeps_query(self):
        value, violation = exclude_contradicted_stereotype("Query", has_writes=False)
        self.assertEqual("Query", value)
        self.assertIsNone(violation)

    def test_write_stereotypes_survive_write_evidence(self):
        value, violation = exclude_contradicted_stereotype("Command", has_writes=True)
        self.assertEqual("Command", value)
        self.assertIsNone(violation)

    def test_priority_text_renders_ontology_order(self):
        # 데이터(ontology)가 정본 — 프롬프트 주입 문자열이 그 순서를 그대로 낸다.
        self.assertEqual(
            "Command › Adapter › Query › Validator › Translator › Helper",
            stereotype_priority_text(),
        )

if __name__ == "__main__":
    unittest.main()
