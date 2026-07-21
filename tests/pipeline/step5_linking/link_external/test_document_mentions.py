from __future__ import annotations

import hashlib
import unittest

from graph.schema.graph_nodes import CodeNode, DocumentNode
from pipeline.steps.step5_linking.link_external.document_mentions import link_exact_document_mentions
from pipeline.steps.step5_linking.link_external.linker import link_external


def _doc(text: str) -> DocumentNode:
    return DocumentNode(
        id="file:shop/guide.md:chunk:doc",
        label="DOC_CHUNK",
        name="chunk 1",
        title="",
        text=text,
        file_path="guide.md",
        start_line=7,
        end_line=7,
        order=1,
        parent_id="file:shop/guide.md:chunk:root",
        content_hash=hashlib.sha256(text.encode()).hexdigest(),
    )


class DocumentMentionsTest(unittest.TestCase):
    def test_linker_emits_document_mention_as_external_fact(self) -> None:
        code = CodeNode(
            "shop.Order.saveOrder", "saveOrder()", "Order.java", 10, 20,
            "METHOD", name="saveOrder", owner_id="shop.Order",
        )
        document = _doc("saveOrder 처리 순서")
        outcome = link_external(
            code_nodes=[code],
            modules=[],
            variables=[],
            constants=[],
            chunks=[],
            tables=[],
            routine_ids={code.id},
            document_nodes=[document],
        )
        self.assertEqual([(document.id, code.id)], [
            (edge.source_id, edge.target_id) for edge in outcome.external_edges
        ])

    def test_unique_identifier_and_fqn_are_exact_mentions(self) -> None:
        code = CodeNode(
            id="shop.OrderService.saveOrder",
            signature="saveOrder(Order)",
            file_path="src/OrderService.java",
            start_line=10,
            end_line=20,
            label="METHOD",
            name="saveOrder",
            owner_id="shop.OrderService",
        )
        edges = link_exact_document_mentions(
            [_doc("OrderService의 saveOrder 호출 순서를 설명한다.")],
            [code],
            {code.id},
        )
        self.assertEqual(
            [("file:shop/guide.md:chunk:doc", code.id, "saveOrder")],
            [(edge.source_id, edge.target_id, edge.matched_text) for edge in edges],
        )

    def test_substring_and_ambiguous_short_name_do_not_become_facts(self) -> None:
        nodes = [
            CodeNode("a.save", "save()", "a.java", 1, 2, "METHOD", name="save", owner_id="a"),
            CodeNode("b.save", "save()", "b.java", 1, 2, "METHOD", name="save", owner_id="b"),
        ]
        self.assertEqual([], link_exact_document_mentions([_doc("autosave와 save를 설명")], nodes, {n.id for n in nodes}))


if __name__ == "__main__":
    unittest.main()
