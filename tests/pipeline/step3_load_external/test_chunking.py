from __future__ import annotations

import hashlib
import unittest

from graph.schema.node_ids import project_file_id
from pipeline.steps.step3_load_external.chunking import MAX_CHUNK_CHARS, make_external_chunks


class ExternalChunkingTest(unittest.TestCase):
    def test_budget_reassembly_hash_and_order(self) -> None:
        text = ("SELECT * FROM 주문;\n" * 160) + ("x" * 1500)
        chunks, edges = make_external_chunks(
            file_id=project_file_id("shop", "mapper/order.xml"),
            key="shop.Order.select",
            kind="sql",
            text=text,
            file_path="mapper/order.xml",
            start_line=3,
            end_line=164,
            sql_type="select",
        )
        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(len(chunk.text) <= MAX_CHUNK_CHARS for chunk in chunks))
        self.assertEqual(text, "".join(chunk.text for chunk in chunks))
        self.assertEqual(list(range(len(chunks))), [chunk.part_index for chunk in chunks])
        self.assertEqual("shop.Order.select", chunks[0].key)
        self.assertTrue(all(not chunk.key for chunk in chunks[1:]))
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        self.assertEqual({digest}, {chunk.content_hash for chunk in chunks})
        self.assertEqual(list(range(1, len(chunks))), [edge.part_index for edge in edges])

    def test_short_text_is_only_an_anchor(self) -> None:
        chunks, edges = make_external_chunks(
            file_id=project_file_id("p", "a.json"), key="a.b", kind="config",
            text="value", file_path="a.json", start_line=1, end_line=1,
        )
        self.assertEqual(1, len(chunks))
        self.assertEqual([], edges)


if __name__ == "__main__":
    unittest.main()
