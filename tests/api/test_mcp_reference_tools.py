"""MCP search list and current semantic-detail contracts."""
from __future__ import annotations

import asyncio
import json
import unittest
from unittest.mock import patch

from api.mcp_app import (
    _list_clusters,
    _select_database,
    cluster_retrieve,
    node_detail,
)
from search.corpus.node_detail import build_node_detail, build_semantic_detail
from shared.config.settings import SETTINGS


def _clusters() -> list:
    rule = {
        "anchor_line": 17,
        "structural_condition": 'F("stock") < F("required")',
        "structural_effects": ['call("reject_order", None, [])'],
        "business_effects": [
            'meaning_call("reject_order", None, [], "주문을 거절한다")'
        ],
        "tables": [{"name": "orders", "op": "READ"}],
        "examples": [{
            "kind": "satisfy",
            "given_conditions": ["g1"],
            "expected_effects": ["t1"],
        }],
    }
    anchor = {
        "id": "code:a.c:fa",
        "element_id": "e1",
        "name": "fa",
        "label": "FUNCTION",
        "stereotype": "svc",
        "summary": "요약A",
        "writes": ["orders"],
        "reads": [],
        "calls": ["fb"],
        "rules": [rule],
        "relevance": 0.9,
        "role": "anchor",
    }
    evidence = {
        "id": "code:a.c:fb",
        "element_id": "e2",
        "name": "fb",
        "label": "FUNCTION",
        "stereotype": "",
        "summary": "요약B",
        "writes": [],
        "reads": ["orders"],
        "calls": [],
        "rules": [dict(rule)],
        "relevance": 0.7,
        "role": "evidence",
        "evidence": [{
            "anchor_id": "code:a.c:fa",
            "relation": "CALLS",
            "direction": "out",
        }],
    }
    table = {
        "id": "db:orders",
        "element_id": "e3",
        "name": "주문",
        "physical_name": "orders",
        "label": "TABLE",
        "stereotype": "",
        "summary": "주문 테이블",
        "writes": [],
        "reads": [],
        "calls": [],
        "rules": [],
        "relevance": 0.9,
        "role": "evidence",
        "evidence": [{
            "anchor_id": "code:a.c:fa",
            "relation": "WRITES",
            "direction": "out",
        }],
    }
    return [{"score": 0.9, "nodes": [anchor, evidence, table]}]


class TestMcpDatabaseScope(unittest.TestCase):
    @patch("api.mcp_app.set_override")
    def test_omitted_database_uses_server_default(self, mocked):
        _select_database(None)
        self.assertEqual(
            SETTINGS.graph_database.database,
            mocked.call_args.args[0].database,
        )

    @patch("api.mcp_app.set_override")
    def test_explicit_database_is_used_verbatim(self, mocked):
        _select_database("framework")
        self.assertEqual("framework", mocked.call_args.args[0].database)


class ListProfileTests(unittest.TestCase):
    _BY_ID = {
        "code:a.c:fa": {"logical_name": "기능A"},
        "code:a.c:fb": {"logical_name": ""},
    }

    def test_list_is_compact_and_preserves_anchor_rule(self):
        listed = _list_clusters(_clusters(), self._BY_ID)[0]["nodes"]
        forbidden = {
            "columns",
            "embedding",
            "element_id",
            "analysis_scope_id",
            "generation_id",
        }
        for node in listed:
            self.assertFalse(forbidden & set(node))
        self.assertEqual("code:a.c:fa", listed[0]["id"])
        self.assertEqual("기능A", listed[0]["logical_name"])
        self.assertEqual(17, listed[0]["rules"][0]["line"])
        self.assertEqual([], listed[1]["rules"])
        self.assertEqual("orders", listed[2]["physical_name"])

    def test_empty_query_is_compact_structured_error(self):
        output = asyncio.run(cluster_retrieve(query=""))
        self.assertEqual("EMPTY_QUERY", json.loads(output)["error"]["code"])
        self.assertNotIn("\\u", output)
        self.assertNotIn("\n", output)


def _function_properties(node_id: str, name: str, summary: str) -> dict:
    return {
        "stable_id": node_id,
        "name": name,
        "qualified_name": node_id,
        "code_kind": "FUNCTION",
        "summary": summary,
        "stereotype": "Service",
        "meaning_status": "sufficient",
        "missing_context_count": 0,
        "analysis_scope_id": "scope:test",
        "generation_id": "generation:test",
    }


class DetailPayloadTests(unittest.TestCase):
    def test_current_code_detail_hides_storage_fields(self):
        payload = build_node_detail(
            [{"labels": ["CodeNode"], "props": _function_properties(
                "code:a.c:fa", "fa", "주문을 처리한다",
            )}],
            [{
                "type": "CALLS",
                "direction": "out",
                "other_id": "code:a.c:fb",
                "other_name": "fb",
                "other_labels": ["CodeNode"],
                "other_properties": _function_properties(
                    "code:a.c:fb", "fb", "재고를 확인한다",
                ),
                "properties": {
                    "analysis_scope_id": "scope:test",
                    "generation_id": "generation:test",
                },
            }],
            [],
            [],
        )
        node = payload["node"]
        self.assertEqual("code:a.c:fa", node["id"])
        self.assertNotIn("source", node)
        self.assertFalse(
            {"stable_id", "analysis_scope_id", "generation_id", "embedding"}
            & set(node["properties"])
        )

    def test_semantic_detail_contains_callee_meaning_without_evidence_metadata(self):
        detail = build_node_detail(
            [{"labels": ["CodeNode"], "props": _function_properties(
                "code:a.c:fa", "fa", "주문을 처리한다",
            )}],
            [
                {
                    "type": "CALLS",
                    "direction": "out",
                    "other_id": "code:a.c:fb",
                    "other_name": "fb",
                    "other_labels": ["CodeNode"],
                    "other_properties": _function_properties(
                        "code:a.c:fb", "fb", "재고를 확인한다",
                    ),
                    "properties": {},
                },
                {
                    "type": "CALLS",
                    "direction": "in",
                    "other_id": "code:a.c:caller",
                    "other_name": "caller",
                    "other_labels": ["CodeNode"],
                    "other_properties": _function_properties(
                        "code:a.c:caller", "caller", "상위 흐름",
                    ),
                    "properties": {},
                },
            ],
            [{
                "rule_id": "R-12",
                "anchor_line": 12,
                "structural_condition": 'F("status") == "PAID"',
                "structural_effects": ['call("save", None, [])'],
                "business_effects": ['meaning_call("save", None, [], "저장한다")'],
                "examples": [{
                    "kind": "satisfy",
                    "given_conditions": ['F("status") == "PAID"'],
                    "expected_effects": ['call("save", None, [])'],
                }],
                "tables": [],
            }],
            [],
        )
        packet = build_semantic_detail(detail, [])
        self.assertEqual("semantic-detail/v3", packet["schema_version"])
        [callee] = packet["node"]["linked_context"]["callees"]
        self.assertEqual("재고를 확인한다", callee["summary"])
        self.assertNotIn("evidence_id", json.dumps(packet, ensure_ascii=False))
        self.assertNotIn("source_anchor", json.dumps(packet, ensure_ascii=False))
        self.assertNotIn("missing_context_artifact_hash", json.dumps(packet))
        self.assertNotIn("missing_context_count", json.dumps(packet))
        self.assertNotIn("symbols", packet["node"]["linked_context"])
        self.assertNotIn("meta", packet)
        self.assertEqual(12, packet["node"]["rules"][0]["line"])
        self.assertEqual(
            detail["node"]["rules"][0]["business_effects"],
            packet["node"]["rules"][0]["business_effects"],
        )
        self.assertNotIn("rule_examples", packet["node"])

    def test_table_and_column_use_current_product_names(self):
        for label, properties in (
            ("Table", {
                "stable_id": "db:orders",
                "physical_table_name": "orders",
                "logical_name": "주문",
                "analyzed_description": "주문 저장소",
                "meaning_status": "sufficient",
                "missing_context_count": 0,
            }),
            ("Column", {
                "stable_id": "db:orders.status",
                "physical_column_name": "status",
                "meaning_status": "insufficient",
                "missing_context_count": 1,
                "missing_context_artifact_hash": "c" * 64,
            }),
        ):
            with self.subTest(label=label):
                detail = build_node_detail(
                    [{"labels": [label], "props": properties}],
                    [],
                    [],
                    [],
                )
                packet = build_semantic_detail(detail, [])
                self.assertEqual(
                    properties["meaning_status"],
                    packet["node"]["data_object_meaning"]["meaning_status"],
                )

    def test_invalid_view_and_identity_errors_are_explicit(self):
        output = asyncio.run(node_detail(node_id="x", view="full"))
        self.assertEqual("INVALID_VIEW", json.loads(output)["error"]["code"])
        self.assertEqual(
            "EMPTY_ID",
            build_node_detail([], [], [], [], node_id="")["error"]["code"],
        )
        self.assertEqual(
            "NODE_NOT_FOUND",
            build_node_detail([], [], [], [], node_id="x")["error"]["code"],
        )
        duplicate = [{"labels": ["CodeNode"], "props": {"stable_id": "x"}}] * 2
        self.assertEqual(
            "NODE_ID_NOT_UNIQUE",
            build_node_detail(duplicate, [], [], [], node_id="x")["error"]["code"],
        )


if __name__ == "__main__":
    unittest.main()
