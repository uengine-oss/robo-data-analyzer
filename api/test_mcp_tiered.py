"""spec 057 — 계층화 프로파일(_tiered_clusters)·직렬화 단위테스트 (data-model.md V1~V6).

V7(detail='full'/'summary' 원형 보존)은 실DB 경유라 quickstart §3 실측에서 확인한다.
"""
from __future__ import annotations

import asyncio
import json
import unittest
from unittest.mock import patch

from api.mcp_app import _merge_rules, _tiered_clusters, cluster_retrieve


def _clusters() -> list:
    rule = {"statement": "재고 부족 시 거절", "examples": [
        {"given": "g1", "when": "w1", "then": "t1", "tables": [{"name": "orders", "op": "R"}]},
    ]}
    rule_dup = {"statement": "재고 부족 시 거절", "examples": [
        {"given": "g2", "when": "w2", "then": "t2", "tables": []},
    ]}
    anchor = {
        "id": "code:a.c:fa", "element_id": "e1", "name": "fa", "label": "FUNCTION",
        "stereotype": "svc", "summary": "요약A", "search_text": "내부",
        "writes": ["orders"], "reads": [], "calls": ["fb"],
        "rules": [rule, rule_dup], "relevance": 0.9, "role": "anchor",
    }
    ev = {
        "id": "code:a.c:fb", "element_id": "e2", "name": "fb", "label": "FUNCTION",
        "stereotype": "", "summary": "요약B", "search_text": "내부",
        "writes": [], "reads": ["orders"], "calls": [],
        "rules": [dict(rule)], "relevance": 0.7, "role": "evidence",
        "evidence": [{"anchor_id": "code:a.c:fa", "relation": "CALLS", "direction": "out"}],
    }
    tbl = {
        "id": "db:orders", "element_id": "e3", "name": "주문", "physical_name": "orders",
        "label": "TABLE", "stereotype": "", "summary": "주문 테이블", "search_text": "",
        "writes": [], "reads": [], "calls": [], "rules": [],
        "relevance": 0.9, "role": "evidence",
        "evidence": [{"anchor_id": "code:a.c:fa", "relation": "WRITES", "direction": "out"}],
    }
    return [{"score": 0.9, "nodes": [anchor, ev, tbl]}]


_BY_ID = {
    "code:a.c:fa": {"code_text": "int fa(){}", "logical_name": "기능A"},
    "code:a.c:fb": {"code_text": "int fb(){}", "logical_name": ""},
}
_COLS = {"db:orders": [{"name": "order_id", "dtype": "NUMBER", "is_primary_key": True,
                        "logical_name": "주문ID", "description": "주문 식별자"}]}


class TieredProfileTests(unittest.TestCase):
    def _tiered(self):
        with patch("api.mcp_app._table_columns", return_value=_COLS):
            return _tiered_clusters(_clusters(), _BY_ID)

    def test_v1_no_internal_fields_anywhere(self):
        for node in self._tiered()[0]["nodes"]:
            self.assertNotIn("search_text", node)
            self.assertNotIn("element_id", node)

    def test_v2_all_functions_carry_code_text(self):
        fns = [n for n in self._tiered()[0]["nodes"] if n["label"] == "FUNCTION"]
        self.assertEqual(2, len(fns))
        for fn in fns:
            self.assertIn("code_text", fn)
        self.assertEqual("int fa(){}", fns[0]["code_text"])
        self.assertEqual("기능A", fns[0]["logical_name"])

    def test_v3_table_carries_full_columns(self):
        tbl = next(n for n in self._tiered()[0]["nodes"] if n["label"] == "TABLE")
        self.assertEqual(1, len(tbl["columns"]))
        col = tbl["columns"][0]
        for key in ("name", "dtype", "is_primary_key", "logical_name", "description"):
            self.assertIn(key, col)
        for key in ("stereotype", "writes", "reads", "calls"):
            self.assertNotIn(key, tbl)

    def test_v4_anchor_rules_full_evidence_rules_empty(self):
        nodes = self._tiered()[0]["nodes"]
        anchor = next(n for n in nodes if n["role"] == "anchor")
        ev = next(n for n in nodes if n["label"] == "FUNCTION" and n["role"] == "evidence")
        self.assertEqual(2, sum(len(r["examples"]) for r in anchor["rules"]))  # 전량 보존
        self.assertEqual([], ev["rules"])                                      # 형태 안정
        self.assertEqual("CALLS", ev["evidence"][0]["relation"])               # 포함 근거 유지

    def test_v5_duplicate_statements_merged_in_order(self):
        anchor = next(n for n in self._tiered()[0]["nodes"] if n["role"] == "anchor")
        self.assertEqual(1, len(anchor["rules"]))
        examples = anchor["rules"][0]["examples"]
        self.assertEqual(["g1", "g2"], [e["given"] for e in examples])  # 합집합·순서 보존

    def test_v5b_merge_rules_helper_keeps_distinct_statements(self):
        merged = _merge_rules([
            {"statement": "A", "examples": [1]},
            {"statement": "B", "examples": [2]},
            {"statement": "A", "examples": [3]},
        ])
        self.assertEqual(["A", "B"], [r["statement"] for r in merged])
        self.assertEqual([1, 3], merged[0]["examples"])

    def test_v6_tool_returns_compact_unescaped_text(self):
        out = asyncio.run(cluster_retrieve(query=""))
        self.assertIsInstance(out, str)
        self.assertEqual({"error": "빈 질의"}, json.loads(out))
        self.assertNotIn("\\u", out)   # 한글 비이스케이프
        self.assertNotIn("\n", out)    # compact(들여쓰기 없음)


if __name__ == "__main__":
    unittest.main()
