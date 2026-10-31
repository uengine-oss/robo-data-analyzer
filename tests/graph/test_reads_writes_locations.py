from __future__ import annotations

import json
import unittest

from graph.edges.reads_writes import READS_QUERY, WRITES_QUERY, _to_items, merge_existing
from graph.schema.graph_edges import ReadsWrites


class ReadsWritesLocationTest(unittest.TestCase):
    def test_groups_topology_but_preserves_sorted_exact_locations(self):
        items = _to_items(
            [
                ReadsWrites("fn", "table", "READS", "SELECT", "a.c", 19),
                ReadsWrites("fn", "table", "READS", "SELECT", "a.c", 7),
                ReadsWrites("fn", "table", "READS", "SELECT", "a.c", 19),
            ]
        )

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["evidence_lines"], [7, 19])
        self.assertEqual(
            [json.loads(value) for value in items[0]["evidence_locations"]],
            [
                {"dml_type": "SELECT", "file": "a.c", "line": 7},
                {"dml_type": "SELECT", "file": "a.c", "line": 19},
            ],
        )


    def test_existing_evidence_is_kept_and_new_values_appended_once(self):
        """옛 ``reduce`` 누적과 같은 결과 — 기존 순서 유지, 새 값 중 없는 것만 뒤에."""
        item = _to_items([
            ReadsWrites("fn", "table", "READS", "SELECT", "a.c", 19),
            ReadsWrites("fn", "table", "READS", "SELECT", "b.c", 3),
        ])[0]
        existing = [{
            "source_id": "fn", "target_id": "table",
            "evidence_lines": [19, 40], "evidence_files": ["a.c"],
            "dml_types": ["SELECT"], "evidence_locations": [],
        }]
        merged = merge_existing([item], existing)[0]
        self.assertEqual(merged["evidence_lines"], [19, 40, 3])
        self.assertEqual(merged["evidence_files"], ["a.c", "b.c"])
        self.assertEqual(merged["dml_types"], ["SELECT"])
        self.assertEqual(len(merged["evidence_locations"]), 2)
        self.assertEqual(merge_existing([item], []), [item])

    def test_queries_do_not_use_reduce_or_list_concatenation(self):
        # Ontological DB: reduce 미지원, 목록 + 는 문자열 이어붙이기로 풀린다 (실측).
        for query in (READS_QUERY, WRITES_QUERY):
            self.assertNotIn("reduce(", query)
            self.assertNotIn("+ value", query)
            self.assertIn("r.evidence_lines = item.evidence_lines", query)


if __name__ == "__main__":
    unittest.main()
