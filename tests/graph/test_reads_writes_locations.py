from __future__ import annotations

import json
import unittest

from graph.edges.reads_writes import _to_items
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


if __name__ == "__main__":
    unittest.main()
