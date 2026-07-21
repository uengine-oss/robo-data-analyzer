from __future__ import annotations

import unittest

from pipeline.steps.step5_linking.link_external.sql_tables import extract_table_refs


class SqlTableExtractionTest(unittest.TestCase):
    def test_cte_comments_strings_reserved_and_write_targets(self) -> None:
        sql = """
/* DELETE FROM SECRET */
WITH recent(id) AS (SELECT id FROM ORDERS),
     joined AS (SELECT r.id FROM recent r JOIN CUSTOMERS c ON c.id=r.id)
SELECT * FROM joined WHERE note='MERGE INTO SECRET USING HIDDEN';
UPDATE ORDER_TOTAL SET amount=1;
DELETE FROM OLD_ORDER;
MERGE INTO ORDER_AUDIT a USING ORDER_TOTAL t ON a.id=t.id WHEN MATCHED THEN UPDATE SET a.x=1;
"""
        triples = {(ref.schema, ref.name, ref.op) for ref in extract_table_refs(sql)}
        self.assertIn(("", "ORDERS", "SELECT"), triples)
        self.assertIn(("", "CUSTOMERS", "SELECT"), triples)
        self.assertIn(("", "ORDER_TOTAL", "UPDATE"), triples)
        self.assertIn(("", "OLD_ORDER", "DELETE"), triples)
        self.assertIn(("", "ORDER_AUDIT", "MERGE"), triples)
        self.assertIn(("", "ORDER_TOTAL", "SELECT"), triples)
        self.assertNotIn(("", "RECENT", "SELECT"), triples)
        self.assertNotIn(("", "JOINED", "SELECT"), triples)
        self.assertFalse(any(name in {"SECRET", "HIDDEN", "FROM"} for _, name, _ in triples))

    def test_update_requires_set_and_delete_is_not_double_read(self) -> None:
        refs = extract_table_refs("log UPDATE fail; DELETE FROM ORDERS WHERE id=1")
        self.assertEqual([("ORDERS", "DELETE", "WRITES")], [(r.name, r.op, r.relation) for r in refs])


if __name__ == "__main__":
    unittest.main()
