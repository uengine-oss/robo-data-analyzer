"""MCP search-list and direct product-node detail contracts."""
from __future__ import annotations

import asyncio
import json
import unittest
from unittest.mock import patch

from api.mcp_app import _list_clusters, _select_database, cluster_retrieve, node_detail
from search.corpus.node_detail import build_node_detail, build_semantic_detail
from shared.config.app_settings import SETTINGS


def _rule(line: int = 17) -> dict:
    return {
        "rule_id": f"routine::R-{line}",
        "line": line,
        "condition": 'F("stock") < F("required")',
        "effects": ['call("reject_order", None, [])'],
        "condition_description": "재고가 요청 수량보다 적을 때",
        "effect_descriptions": ["주문을 거절한다"],
    }


def _clusters() -> list:
    anchor = {
        "id": "code:a.c:fa", "element_id": "e1", "name": "fa",
        "label": "FUNCTION", "stereotype": "svc", "summary": "주문을 처리한다",
        "writes": ["orders"], "reads": [], "calls": ["fb"], "rules": [_rule()],
        "relevance": 0.9, "role": "anchor",
    }
    evidence = {
        "id": "code:a.c:fb", "element_id": "e2", "name": "fb",
        "label": "FUNCTION", "stereotype": "", "summary": "재고를 확인한다",
        "writes": [], "reads": ["orders"], "calls": [], "rules": [_rule()],
        "relevance": 0.7, "role": "evidence",
        "evidence": [{"anchor_id": "code:a.c:fa", "relation": "CALLS", "direction": "out"}],
    }
    table = {
        "id": "db:orders", "element_id": "e3", "name": "주문",
        "physical_name": "orders", "label": "TABLE", "stereotype": "",
        "summary": "주문 테이블", "writes": [], "reads": [], "calls": [],
        "rules": [], "relevance": 0.9, "role": "evidence",
        "evidence": [{"anchor_id": "code:a.c:fa", "relation": "WRITES", "direction": "out"}],
    }
    return [{"score": 0.9, "nodes": [anchor, evidence, table]}]


def _function_properties(node_id: str, name: str, summary: str) -> dict:
    return {
        "_id": node_id, "_owner": "analyzer", "name": name, "file_path": "a.c",
        "start_line": 1, "end_line": 3,
        "code_text": "1 | void fa() {\n2 |   return;\n3 | }",
        "summary": summary, "stereotype": "Service",
    }


class TestMcpDatabaseScope(unittest.TestCase):
    @patch("api.mcp_app.set_override")
    def test_omitted_database_uses_server_default(self, mocked):
        _select_database(None)
        self.assertEqual(SETTINGS.graph_database.database, mocked.call_args.args[0].database)

    @patch("api.mcp_app.set_override")
    def test_explicit_database_is_used_verbatim(self, mocked):
        _select_database("framework")
        self.assertEqual("framework", mocked.call_args.args[0].database)


class ListProfileTests(unittest.TestCase):
    def test_list_is_compact_and_preserves_only_anchor_rules(self):
        listed = _list_clusters(_clusters())[0]["nodes"]
        self.assertEqual(
            {"id", "name", "label", "stereotype", "summary", "writes",
             "reads", "calls", "rules", "relevance", "role"},
            set(listed[0]),
        )
        self.assertEqual(
            {"id", "name", "label", "stereotype", "summary", "writes",
             "reads", "calls", "rules", "relevance", "role", "evidence"},
            set(listed[1]),
        )
        self.assertEqual(
            {"id", "name", "physical_name", "label", "summary", "rules",
             "relevance", "role", "evidence"},
            set(listed[2]),
        )
        self.assertEqual("code:a.c:fa", listed[0]["id"])
        self.assertEqual(17, listed[0]["rules"][0]["line"])
        self.assertEqual([], listed[1]["rules"])
        self.assertEqual("orders", listed[2]["physical_name"])

    def test_empty_query_is_compact_structured_error(self):
        output = asyncio.run(cluster_retrieve(query=""))
        self.assertEqual("EMPTY_QUERY", json.loads(output)["error"]["code"])
        self.assertNotIn("\\u", output)
        self.assertNotIn("\n", output)


class DetailPayloadTests(unittest.TestCase):
    def test_current_code_detail_hides_storage_fields(self):
        payload = build_node_detail(
            [{"labels": ["FUNCTION"], "props": _function_properties(
                "code:a.c:fa", "fa", "주문을 처리한다",
            )}],
            [{
                "type": "CALLS", "direction": "out", "other_id": "code:a.c:fb",
                "other_name": "fb", "other_labels": ["FUNCTION"],
                "other_properties": _function_properties(
                    "code:a.c:fb", "fb", "재고를 확인한다",
                ),
                "properties": {"_owner": "analyzer"},
            }],
            [], [], node_id="code:a.c:fa",
        )
        node = payload["node"]
        self.assertEqual("code:a.c:fa", node["id"])
        self.assertNotIn("source", node)
        self.assertFalse({"_id", "_owner", "_search_embedding"} & set(node["properties"]))
        self.assertEqual({}, node["relationships"][0]["properties"])

    def test_semantic_detail_contains_callee_meaning_and_current_rules(self):
        detail = build_node_detail(
            [{"labels": ["FUNCTION"], "props": _function_properties(
                "code:a.c:fa", "fa", "주문을 처리한다",
            )}],
            [{
                "type": "CALLS", "direction": "out", "other_id": "code:a.c:fb",
                "other_name": "fb", "other_labels": ["FUNCTION"],
                "other_properties": _function_properties(
                    "code:a.c:fb", "fb", "재고를 확인한다",
                ),
                "properties": {},
            }],
            [_rule(12)], [], node_id="code:a.c:fa",
        )
        packet = build_semantic_detail(detail, [])
        self.assertEqual("semantic-detail/v4", packet["schema_version"])
        [callee] = packet["node"]["linked_context"]["callees"]
        self.assertEqual("재고를 확인한다", callee["summary"])
        self.assertEqual(12, packet["node"]["rules"][0]["line"])
        self.assertEqual(
            detail["node"]["rules"][0]["effect_descriptions"],
            packet["node"]["rules"][0]["effect_descriptions"],
        )
        rendered = json.dumps(packet, ensure_ascii=False)
        self.assertNotIn("evidence_id", rendered)
        self.assertEqual(
            {"id", "labels", "name", "properties", "rules",
             "function_meaning", "linked_context"},
            set(packet["node"]),
        )

    def test_table_and_column_use_direct_product_properties(self):
        cases = (
            ("TABLE", {
                "_id": "db:orders", "_owner": "analyzer", "name": "orders",
                "logical_name": "주문", "summary": "주문 저장소",
            }),
            ("COLUMN", {
                "_id": "db:orders.status", "_owner": "analyzer", "name": "status",
                "summary": "주문 상태",
            }),
        )
        for label, properties in cases:
            with self.subTest(label=label):
                detail = build_node_detail(
                    [{"labels": [label], "props": properties}], [], [], [],
                    node_id=properties["_id"],
                )
                packet = build_semantic_detail(detail, [])
                self.assertEqual(
                    properties["summary"],
                    packet["node"]["data_object_meaning"]["summary"],
                )

    def test_invalid_view_and_identity_errors_are_explicit(self):
        output = asyncio.run(node_detail(node_id="x", view="full"))
        self.assertEqual("INVALID_VIEW", json.loads(output)["error"]["code"])
        self.assertEqual("EMPTY_ID", build_node_detail(
            [], [], [], [], node_id="",
        )["error"]["code"])
        self.assertEqual("NODE_NOT_FOUND", build_node_detail(
            [], [], [], [], node_id="x",
        )["error"]["code"])
        duplicate = [{"labels": ["FUNCTION"], "props": {"_id": "x"}}] * 2
        self.assertEqual("NODE_ID_NOT_UNIQUE", build_node_detail(
            duplicate, [], [], [], node_id="x",
        )["error"]["code"])


if __name__ == "__main__":
    unittest.main()
