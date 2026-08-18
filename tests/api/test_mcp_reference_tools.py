"""spec 060 — MCP 목록·상세 분리와 compact 문자열 계약."""
from __future__ import annotations

import asyncio
import json
import unittest
from unittest.mock import patch

from api.mcp_app import _list_clusters, _select_database, cluster_retrieve, node_detail
from shared.config.settings import SETTINGS
from search.corpus.node_detail import build_gwt_detail, build_node_detail
from pipeline.models.semantic_frame import (
    EvidenceFact,
    SemanticFrame,
    SemanticSlot,
    StructuralProfile,
    StructuralTarget,
    evidence_id,
    slot_id,
)


def _clusters() -> list:
    rule = {
        "anchor_line": 17,
        "cond": 'F("stock") < F("required")',
        "then": ['call("reject_order")'],
        "nl": "",
        "tables": [
        {"name": "orders", "op": "READ"},
    ], "examples": [
        {"given": "g1", "when": "w1", "then": "t1", "tables": [{"name": "orders", "op": "R"}]},
    ]}
    rule_dup = {
        "anchor_line": 17,
        "cond": 'F("stock") < F("required")',
        "then": ['call("reject_order")'],
        "nl": "",
        "tables": [
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


class TestMcpDatabaseScope(unittest.TestCase):
    @patch("api.mcp_app.set_override")
    def test_omitted_database_resets_previous_request_to_server_default(self, mocked):
        _select_database(None)
        override = mocked.call_args.args[0]
        self.assertEqual(SETTINGS.graph_database.database, override.database)

    @patch("api.mcp_app.set_override")
    def test_explicit_database_is_used_verbatim(self, mocked):
        _select_database("spec131fw1520260812")
        override = mocked.call_args.args[0]
        self.assertEqual("spec131fw1520260812", override.database)


_BY_ID = {
    "code:a.c:fa": {"code_text": "int fa(){}", "logical_name": "기능A"},
    "code:a.c:fb": {"code_text": "int fb(){}", "logical_name": ""},
}


def _frame_json(
    node_id: str = "code:a.c:fa",
    *,
    label: str = "FUNCTION",
    meaning: str = "주문을 처리한다",
    status: str = "sufficient",
    missing_context: tuple[str, ...] = (),
) -> str:
    is_code = label in {"FUNCTION", "PROCEDURE", "METHOD", "TRIGGER"}
    target = StructuralTarget(
        id=node_id,
        type=label,
        owner_id="code:a.c" if is_code else "db:orders",
        file="a.c" if is_code else "(catalog)",
        start_line=10 if is_code else 0,
        end_line=20 if is_code else 0,
    )
    target_ref = evidence_id(node_id, "TARGET", 0)
    profile = StructuralProfile(
        target=target,
        evidence={target_ref: EvidenceFact(
            id=target_ref,
            kind="TARGET",
            owner_id=node_id,
            file=target.file,
            start_line=target.start_line,
            end_line=target.end_line,
            attributes={"type": label, "signature": "", "name": node_id,
                        "params": "", "return_type": ""},
        )},
    )
    family = label if label in {"TABLE", "COLUMN"} else "FUNCTION"
    role = "DESCRIPTION" if family in {"TABLE", "COLUMN"} else "RESPONSIBILITY"
    sid = slot_id(node_id, family, role, "TARGET")
    frame = SemanticFrame(
        target=target,
        profile=profile,
        slots={sid: SemanticSlot(
            slot_id=sid,
            family=family,
            role=role,
            target_ref=target_ref,
            evidence_refs=(target_ref,),
            meaning=meaning,
            status=status,
            missing_context=missing_context,
        )},
    )
    return frame.canonical_json()


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
            "text": '조건: F("stock") < F("required") → 효과: call("reject_order")',
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

    def test_frame_view_excludes_source_and_preserves_frame_support_context(self):
        full = build_node_detail(
            [{"labels": ["FUNCTION"], "props": {
                "id": "code:a.c:fa", "name": "fa", "summary": "주문을 처리한다",
                "logical_name": "주문 처리", "stereotype": "Service",
                "summary_evidence_status": "partial",
                "summary_missing_context_json": '["외부 결제 계약"]',
                "file_path": "a.c", "start_line": 10, "end_line": 20,
                "code_text": "int fa(){ /* long body */ }",
                "semantic_frame_json": _frame_json(),
            }}],
            [{"type": "CALLS", "direction": "out", "other_id": "code:a.c:fb",
              "other_name": "fb", "other_labels": ["FUNCTION"], "properties": {}}],
            [{"rule_id": "R-12", "anchor_line": 12,
              "cond": 'F("status == PAID")', "then": ['select("orders")'],
              "when": "fa", "nl": "결제 상태를 확인한다", "tables": [
                {"name": "orders", "op": "select"},
            ], "examples": [{"kind": "satisfy", "given": ["status == PAID"],
                              "then": ['select("orders")']}]}], [],
        )
        compact = build_gwt_detail(full, [{
            "id": "db:orders", "name": "orders", "access": ["READ"],
            "evidence_status": "sufficient", "missing_context": [],
            "columns": [{"name": "status", "dtype": "TEXT"}],
            "sample": {"columns": [{"name": "status"}],
                       "sample_rows": [{"status": "PAID"}]},
        }])
        node = compact["node"]
        self.assertEqual("semantic-frame-packet/v1", compact["schema_version"])
        self.assertNotIn("source", node)
        self.assertEqual("semantic-frame/v1", node["semantic_frame"]["schema"])
        self.assertEqual("code:a.c:fb", node["linked_context"]["callees"][0]["id"])
        self.assertEqual("code:a.c:fa::call::out::code:a.c:fb",
                         node["linked_context"]["callees"][0]["evidence_id"])
        self.assertEqual("satisfy", node["rule_examples"][0]["examples"][0]["kind"])
        self.assertEqual("PAID", node["linked_context"]["data_objects"][0]["sample"]["sample_rows"][0]["status"])
        self.assertEqual("code:a.c:fa::table::db:orders",
                         node["linked_context"]["data_objects"][0]["evidence_id"])

    def test_gwt_view_preserves_referenced_symbol_definition(self):
        full = build_node_detail(
            [{"labels": ["FUNCTION"], "props": {
                "id": "code:a.c:fa", "name": "fa", "file_path": "a.c",
                "start_line": 10, "end_line": 20, "code_text": "10 | int fa(){}",
                "semantic_frame_json": _frame_json(),
            }}],
            [{"type": "REFERENCES", "direction": "out",
              "other_id": "code:a.c:LIMIT", "other_name": "LIMIT",
              "other_labels": ["DEFINE"], "properties": {"evidence_line": 12},
              "other_properties": {
                  "id": "code:a.c:LIMIT", "name": "LIMIT", "value": "100",
                  "var_type": "int", "file_path": "a.c", "start_line": 2,
                  "end_line": 2, "code_text": "2 | #define LIMIT 100",
              }}],
            [], [],
        )

        [symbol] = build_gwt_detail(full, [])["node"]["linked_context"]["symbols"]
        self.assertEqual("TARGET_REFERENCES_DEFINITION", symbol["relation"])
        self.assertEqual("100", symbol["definition"]["value"])
        self.assertNotIn("source", symbol)
        self.assertEqual(12, symbol["reference_line"])
        self.assertEqual("code:a.c:fa::symbol::code:a.c:LIMIT::12",
                         symbol["evidence_id"])

    def test_gwt_view_excludes_inbound_callers_from_callee_context(self):
        full = build_node_detail(
            [{"labels": ["FUNCTION"], "props": {
                "id": "code:a.c:fa", "name": "fa", "file_path": "a.c",
                "start_line": 10, "end_line": 20, "code_text": "10 | int fa(){}",
                "semantic_frame_json": _frame_json(),
            }}],
            [
                {"type": "CALLS", "direction": "out",
                 "other_id": "code:a.c:callee", "other_name": "callee",
                 "other_labels": ["FUNCTION"], "properties": {}},
                {"type": "CALLS", "direction": "in",
                 "other_id": "code:a.c:caller", "other_name": "caller",
                 "other_labels": ["FUNCTION"], "properties": {}},
            ],
            [], [],
        )

        calls = build_gwt_detail(full, [])["node"]["linked_context"]["callees"]
        self.assertEqual(["code:a.c:callee"], [call["id"] for call in calls])
        self.assertEqual(["out"], [call["direction"] for call in calls])

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
                        "semantic_frame_json": _frame_json(
                            node_id, label=label, meaning=description,
                            status=status, missing_context=tuple(missing_context),
                        ),
                    }}],
                    [], [], [],
                )
                node = build_gwt_detail(full, [])["node"]
                [slot] = node["semantic_frame"]["slots"].values()
                self.assertEqual(description, slot["meaning"])
                self.assertEqual(status, slot["status"])
                self.assertEqual(missing_context, slot["missing_context"])

    def test_frame_view_does_not_fall_back_to_legacy_description(self):
        full = build_node_detail(
            [{"labels": ["COLUMN"], "props": {
                "id": "db:orders.status", "name": "status",
                "description": "DDL comment description",
                "analyzed_description": "Analyzer description",
                "semantic_frame_json": _frame_json(
                    "db:orders.status", label="COLUMN", meaning="검증된 프레임 설명",
                ),
            }}],
            [], [], [],
        )
        self.assertEqual(
            "검증된 프레임 설명",
            next(iter(build_gwt_detail(full, [])["node"]["semantic_frame"]["slots"].values()))["meaning"],
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
