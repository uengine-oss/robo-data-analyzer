"""spec 060 — MCP 목록·상세 분리와 compact 문자열 계약."""
from __future__ import annotations

import asyncio
import json
import unittest

from api.mcp_app import _list_clusters, cluster_retrieve, node_detail
from search.corpus.node_detail import build_gwt_detail, build_node_detail


def _clusters() -> list:
    rule = {"anchor_line": 17, "nl": "재고 부족 시 거절한다", "tables": [
        {"name": "orders", "op": "READ"},
    ], "examples": [
        {"given": "g1", "when": "w1", "then": "t1", "tables": [{"name": "orders", "op": "R"}]},
    ]}
    rule_dup = {"anchor_line": 17, "nl": "재고 부족 시 거절한다", "tables": [
        {"name": "orders", "op": "READ"},
    ], "examples": [
        {"given": "g2", "when": "w2", "then": "t2", "tables": []},
    ]}
    anchor = {
        "id": "code:a.c:fa", "element_id": "e1", "name": "fa", "label": "FUNCTION",
        "stereotype": "svc", "summary": "요약A",
        "writes": ["orders"], "reads": [], "calls": ["fb"],
        "rules": [rule, rule_dup], "relevance": 0.9, "role": "anchor",
    }
    evidence = {
        "id": "code:a.c:fb", "element_id": "e2", "name": "fb", "label": "FUNCTION",
        "stereotype": "", "summary": "요약B",
        "writes": [], "reads": ["orders"], "calls": [], "rules": [dict(rule)],
        "relevance": 0.7, "role": "evidence",
        "evidence": [{"anchor_id": "code:a.c:fa", "relation": "CALLS", "direction": "out"}],
    }
    table = {
        "id": "db:orders", "element_id": "e3", "name": "주문", "physical_name": "orders",
        "label": "TABLE", "stereotype": "", "summary": "주문 테이블",
        "writes": [], "reads": [], "calls": [], "rules": [], "relevance": 0.9,
        "role": "evidence",
        "evidence": [{"anchor_id": "code:a.c:fa", "relation": "WRITES", "direction": "out"}],
    }
    return [{"score": 0.9, "nodes": [anchor, evidence, table]}]


_BY_ID = {
    "code:a.c:fa": {"code_text": "int fa(){}", "logical_name": "기능A"},
    "code:a.c:fb": {"code_text": "int fb(){}", "logical_name": ""},
}


class ListProfileTests(unittest.TestCase):
    def _listed(self):
        return _list_clusters(_clusters(), _BY_ID)

    def test_list_has_no_detail_or_internal_fields(self):
        forbidden = {"code_text", "columns", "embedding", "element_id", "graph_owner"}
        for node in self._listed()[0]["nodes"]:
            self.assertFalse(forbidden & set(node))

    def test_list_preserves_identity_summary_evidence_and_anchor_rules(self):
        nodes = self._listed()[0]["nodes"]
        anchor = nodes[0]
        evidence = nodes[1]
        table = nodes[2]
        self.assertEqual("code:a.c:fa", anchor["id"])
        self.assertEqual("기능A", anchor["logical_name"])
        self.assertEqual([{
            "line": 17,
            "text": "재고 부족 시 거절한다",
            "tables": [{"name": "orders", "op": "READ"}],
        }], anchor["rules"])
        self.assertEqual([], evidence["rules"])
        self.assertEqual("CALLS", evidence["evidence"][0]["relation"])
        self.assertEqual("orders", table["physical_name"])

    def test_empty_query_is_structured_compact_unescaped_error(self):
        out = asyncio.run(cluster_retrieve(query=""))
        self.assertIsInstance(out, str)
        self.assertEqual("EMPTY_QUERY", json.loads(out)["error"]["code"])
        self.assertNotIn("\\u", out)
        self.assertNotIn("\n", out)


class DetailPayloadTests(unittest.TestCase):
    def test_code_detail_preserves_source_relations_rules_and_excludes_internal(self):
        payload = build_node_detail(
            [{"labels": ["FUNCTION", "EMBEDDED"], "props": {
                "id": "code:a.c:fa", "name": "fa", "summary": "기능",
                "file_path": "a.c", "start_line": 10, "end_line": 20,
                "code_text": "int fa(){}", "embedding": [1.0],
                "graph_owner": "analyzer",
            }}],
            [{"type": "CALLS", "direction": "out", "other_id": "code:a.c:fa",
              "other_name": "fa", "other_labels": ["FUNCTION"], "properties": {}}],
            [{"statement": "규칙", "examples": []}],
            [],
        )
        node = payload["node"]
        self.assertEqual({"available": True, "file_path": "a.c", "start_line": 10,
                          "end_line": 20, "code_text": "int fa(){}"}, node["source"])
        self.assertEqual("CALLS", node["relationships"][0]["type"])
        self.assertEqual("code:a.c:fa", node["relationships"][0]["node"]["id"])
        self.assertEqual([{"statement": "규칙", "examples": []}], node["rules"])
        self.assertFalse({"embedding", "graph_owner"} & set(node["properties"]))

    def test_table_has_explicit_unavailable_source_and_columns(self):
        payload = build_node_detail(
            [{"labels": ["TABLE"], "props": {"id": "db:orders", "name": "orders"}}],
            [], [], [{"name": "order_id", "dtype": "BIGINT"}],
        )
        self.assertEqual({"available": False, "file_path": None, "start_line": None,
                          "end_line": None, "code_text": None}, payload["node"]["source"])
        self.assertEqual("order_id", payload["node"]["columns"][0]["name"])

    def test_gwt_view_removes_source_body_and_keeps_calls_tables_samples(self):
        full = build_node_detail(
            [{"labels": ["FUNCTION"], "props": {
                "id": "code:a.c:fa", "name": "fa", "summary": "주문을 처리한다",
                "logical_name": "주문 처리", "stereotype": "Service",
                "summary_evidence_status": "partial",
                "summary_missing_context_json": '["외부 결제 계약"]',
                "file_path": "a.c", "start_line": 10, "end_line": 20,
                "code_text": "int fa(){ /* long body */ }",
            }}],
            [{"type": "CALLS", "direction": "out", "other_id": "code:a.c:fb",
              "other_name": "fb", "other_labels": ["FUNCTION"], "properties": {}}],
            [{"anchor_line": 12, "nl": "결제 상태를 확인한다", "tables": [
                {"name": "orders", "op": "select"},
            ]}], [],
        )
        compact = build_gwt_detail(full, [{
            "id": "db:orders", "name": "orders", "access": ["READ"],
            "evidence_status": "sufficient", "missing_context": [],
            "columns": [{"name": "status", "dtype": "TEXT"}],
            "sample": {"columns": [{"name": "status"}],
                       "sample_rows": [{"status": "PAID"}]},
        }])
        node = compact["node"]
        self.assertNotIn("code_text", json.dumps(compact, ensure_ascii=False))
        self.assertEqual({"file_path": "a.c", "start_line": 10, "end_line": 20},
                         node["source"])
        self.assertEqual("code:a.c:fb", node["calls"][0]["id"])
        self.assertEqual("partial", node["summary_evidence_status"])
        self.assertEqual(["외부 결제 계약"], node["summary_missing_context"])
        self.assertEqual([{
            "line": 12,
            "text": "결제 상태를 확인한다",
            "tables": [{"name": "orders", "op": "select"}],
        }], node["rules"])
        self.assertEqual("PAID", node["tables"][0]["sample"]["sample_rows"][0]["status"])

    def test_gwt_view_exposes_direct_table_and_column_semantic_evidence(self):
        for label, node_id, description, status, missing_context in [
            ("TABLE", "db:orders", "주문 처리에서 조회하고 갱신하는 저장소", "sufficient", []),
            ("COLUMN", "db:orders.etc", "", "insufficient", ["DDL comment", "code usage"]),
        ]:
            with self.subTest(label=label):
                full = build_node_detail(
                    [{"labels": [label], "props": {
                        "id": node_id,
                        "name": node_id.rsplit(".", 1)[-1],
                        "analyzed_description": description,
                        "semantic_evidence_status": status,
                        "semantic_missing_context": missing_context,
                    }}],
                    [], [], [],
                )
                node = build_gwt_detail(full, [])["node"]
                self.assertEqual(description, node["description"])
                self.assertEqual(status, node["evidence_status"])
                self.assertEqual(missing_context, node["missing_context"])

    def test_gwt_semantic_description_preserves_source_authority(self):
        full = build_node_detail(
            [{"labels": ["COLUMN"], "props": {
                "id": "db:orders.status", "name": "status",
                "description": "DDL comment description",
                "analyzed_description": "Analyzer description",
            }}],
            [], [], [],
        )
        self.assertEqual(
            "DDL comment description",
            build_gwt_detail(full, [])["node"]["description"],
        )

    def test_invalid_view_is_explicit(self):
        out = asyncio.run(node_detail(node_id="x", view="wide"))
        self.assertEqual("INVALID_VIEW", json.loads(out)["error"]["code"])

    def test_empty_missing_duplicate_and_incomplete_source_are_explicit(self):
        self.assertEqual("EMPTY_ID", build_node_detail([], [], [], [], node_id="")["error"]["code"])
        self.assertEqual("NODE_NOT_FOUND", build_node_detail([], [], [], [], node_id="x")["error"]["code"])
        duplicate = [{"labels": ["FUNCTION"], "props": {"id": "x"}}] * 2
        self.assertEqual("NODE_ID_NOT_UNIQUE",
                         build_node_detail(duplicate, [], [], [], node_id="x")["error"]["code"])
        incomplete = [{"labels": ["FUNCTION"], "props": {
            "id": "x", "name": "x", "file_path": "a.c", "start_line": 1,
            "end_line": 2, "code_text": "",
        }}]
        self.assertEqual("SOURCE_INCOMPLETE",
                         build_node_detail(incomplete, [], [], [], node_id="x")["error"]["code"])


if __name__ == "__main__":
    unittest.main()
