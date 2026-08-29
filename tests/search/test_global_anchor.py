from __future__ import annotations

import unittest
from unittest.mock import patch

import numpy as np

from search.config import Config
from search.ranking.retriever import Retriever


def _candidate(cid: str, relevance: float) -> dict:
    return {
        "id": cid, "name": cid, "label": "FUNCTION", "stereotype": "",
        "summary": cid, "writes": [], "reads": [], "calls": [],
        "relevance": relevance, "role": "core",
    }


class _Corpus:
    by_id = {
        cid: {"id": cid, "name": cid, "label": "FUNCTION", "summary": cid,
              "stereotype": ""}
        for cid in ("a", "b", "c", "d", "x")
    }
    fan_in = {}


class _Retriever(Retriever):
    def __init__(self):
        super().__init__(object(), _Corpus())
        self.call_queries: list[str] = []

    def _retrieve_one(self, query, cfg, pool_k, on_progress, *, expand=True):
        self.assert_expand_flags.append(expand)
        return [_candidate("a", .9), _candidate("b", .8), _candidate("c", .7), _candidate("d", .6)]

    def _ensure_vecs(self, ids):
        for cid in ids:
            self._vecs[cid] = np.array([1.0], dtype=np.float32)

    def _ce(self, query, cids, cfg):
        return {cid: {"a": .9, "b": .8, "c": .7, "d": .6}.get(cid, .1) for cid in cids}

    def _cos(self, cid, qv):
        return .6

    def _callees(self, cid):
        return []

    def _call_neighbors(self, cid):
        self.call_queries.append(cid)
        if cid == "a":
            return [
                {"id": "d", "name": "d", "relation": "CALLS", "direction": "out"},
                {"id": "x", "name": "x", "relation": "CALLS", "direction": "in"},
            ]
        if cid == "b":
            return [{"id": "x", "name": "x", "relation": "CALLS", "direction": "out"}]
        return []

    def _table_neighbors(self, cid):
        if cid != "a":
            return []
        return [{
            "id": "db:orders", "eid": "e1", "name": "orders", "logical_name": "주문",
            "summary": "주문 테이블", "relation": "WRITES",
        }]

    def _node_row(self, cid, cfg, qv, score, callees, role):
        row = _candidate(cid, score[cid][1])
        row["role"] = role
        return row

    assert_expand_flags: list[bool] = []


class GlobalAnchorTests(unittest.TestCase):
    def setUp(self):
        _Retriever.assert_expand_flags = []

    @patch("search.ranking.retriever.embedder.encode_texts", return_value=[[1.0]])
    def test_three_anchors_then_one_hop_code_and_table_evidence(self, _encode):
        # 구 동작(P0) 명시 고정 — spec 057 롤백 모드의 회귀 기준선.
        retriever = _Retriever()
        cfg = Config(
            cluster_anchor_top_n=3,
            cluster_evidence_top_n=3,
            cluster_rewrite_mode="off",
            cluster_anchor_gap=1.0,
            cluster_support_policy="all_candidates",
        )
        meta = {}
        nodes = retriever.retrieve("주문 결제", cfg, meta=meta)

        anchors = [node["id"] for node in nodes if node["role"] == "anchor"]
        evidence = {node["id"]: node for node in nodes if node["role"] == "evidence"}
        self.assertEqual(["a", "b", "c"], anchors)
        self.assertEqual({"d", "x", "db:orders"}, set(evidence))
        self.assertEqual({"a", "b", "c", "d"}, set(retriever.call_queries))
        self.assertTrue(all(flag is False for flag in retriever.assert_expand_flags))
        self.assertEqual("TABLE", evidence["db:orders"]["label"])
        self.assertEqual("WRITES", evidence["db:orders"]["evidence"][0]["relation"])
        self.assertEqual(2, len(evidence["x"]["evidence"]))
        self.assertEqual(3, meta["selection"]["anchors"])

    # ── spec 057: 점수절벽 게이트 + 동반 입장 정책 ──

    def _cfg(self, **kw) -> Config:
        base = dict(
            cluster_anchor_top_n=3,
            cluster_evidence_top_n=5,
            cluster_rewrite_mode="off",
        )
        base.update(kw)
        return Config(**base)

    @patch("search.ranking.retriever.embedder.encode_texts", return_value=[[1.0]])
    def test_cliff_gate_keeps_only_top_anchor_and_drops_junk_branches(self, _encode):
        # a=.9 절벽 아래 b=.8·c=.7 — gap 0.05 면 앵커는 a 뿐, b·c 는 직행도 못 한다.
        retriever = _Retriever()
        nodes = retriever.retrieve("주문 결제", self._cfg(
            cluster_anchor_gap=0.05, cluster_support_policy="gated_direct",
            cluster_direct_gap=0.05))
        anchors = [n["id"] for n in nodes if n["role"] == "anchor"]
        self.assertEqual(["a"], anchors)
        ids = {n["id"] for n in nodes}
        self.assertNotIn("b", ids)          # 오답 후보와
        self.assertNotIn("c", ids)
        self.assertEqual({"a"}, set(retriever.call_queries))   # 그 1홉 뿌리 자격도 소멸
        self.assertIn("d", ids)             # 정답 앵커의 1홉은 유지(CALLS 근거)
        self.assertIn("db:orders", ids)     # 앵커 테이블 유지
        d = next(n for n in nodes if n["id"] == "d")
        self.assertEqual({"CALLS"}, {e["relation"] for e in d["evidence"]})

    @patch("search.ranking.retriever.embedder.encode_texts", return_value=[[1.0]])
    def test_tight_scores_keep_multiple_anchors_and_gated_direct(self, _encode):
        # 상위가 촘촘하면(gap 0.25) 복수 앵커 유지 — 복합 질의 재현율 보존.
        # d(.6)는 direct_gap 0.30 이내라 직행(SEMANTIC_SUPPORT)으로 구제된다.
        retriever = _Retriever()
        nodes = retriever.retrieve("주문 결제", self._cfg(
            cluster_anchor_gap=0.25, cluster_support_policy="gated_direct",
            cluster_direct_gap=0.30))
        anchors = [n["id"] for n in nodes if n["role"] == "anchor"]
        self.assertEqual(["a", "b", "c"], anchors)       # 상한 3 유지
        d = next(n for n in nodes if n["id"] == "d")
        self.assertIn("SEMANTIC_SUPPORT", {e["relation"] for e in d["evidence"]})
        self.assertEqual({"a", "b", "c"}, set(retriever.call_queries))  # 1홉 뿌리는 앵커만

    @patch("search.ranking.retriever.embedder.encode_texts", return_value=[[1.0]])
    def test_anchors_only_policy_has_no_direct_entry(self, _encode):
        retriever = _Retriever()
        nodes = retriever.retrieve("주문 결제", self._cfg(
            cluster_anchor_gap=0.05, cluster_support_policy="anchors_only"))
        roles = {n["id"]: n for n in nodes}
        self.assertEqual(["a"], [n["id"] for n in nodes if n["role"] == "anchor"])
        for nid, node in roles.items():
            if node["role"] == "evidence" and node["label"] != "TABLE":
                self.assertNotIn("SEMANTIC_SUPPORT",
                                 {e["relation"] for e in node["evidence"]})

    @patch("search.ranking.retriever.embedder.encode_texts", return_value=[[1.0]])
    def test_unknown_support_policy_raises(self, _encode):
        retriever = _Retriever()
        with self.assertRaises(ValueError):
            retriever.retrieve("주문 결제", self._cfg(cluster_support_policy="oops"))

    @patch("search.ranking.retriever.embedder.encode_texts", return_value=[[1.0]])
    def test_graph_gated_admits_by_distance_and_blocks_far_low_score(self, _encode):
        # a=.9 만 앵커(gap .05). d(.6)·x 는 점수 게이트 밖이지만 앵커와 거리 1~2 → 입장.
        # b(.8)·c(.7)는 점수도 절벽 밖·그래프도 멀다(스텁 미포함) → 완전 배제.
        retriever = _Retriever()
        near = {"d", "x"}
        retriever._hop_distances = lambda aids, bids, h, cfg=None: {
            cid: 1 for cid in bids if cid in near}
        nodes = retriever.retrieve("주문 결제", self._cfg(
            cluster_anchor_gap=0.05, cluster_support_policy="graph_gated",
            cluster_direct_gap=0.05, cluster_hop_gate=2,
            cluster_rank_floor=1))
        ids = {n["id"] for n in nodes}
        self.assertEqual(["a"], [n["id"] for n in nodes if n["role"] == "anchor"])
        self.assertIn("d", ids)             # 거리 자격 직행 + 뿌리 승격
        self.assertIn("x", ids)             # 자격 뿌리의 1홉 + 자신도 거리 자격
        self.assertNotIn("b", ids)
        self.assertNotIn("c", ids)
        self.assertEqual({"a", "d"}, set(retriever.call_queries))  # 뿌리 = 앵커+자격 직행
        d = next(n for n in nodes if n["id"] == "d")
        self.assertIn("SEMANTIC_SUPPORT", {e["relation"] for e in d["evidence"]})

    @patch("search.ranking.retriever.embedder.encode_texts", return_value=[[1.0]])
    def test_graph_gated_rank_floor_preserves_top_ranked_evidence_only(self, _encode):
        # 채택값 r2는 그래프 거리가 없는 2위 후보도 정답 재현율을 위해 직행 근거로 구제한다.
        # 순위 구제 후보는 자신의 이웃을 확장하지 않아 노이즈를 증폭시키지 않는다.
        retriever = _Retriever()
        retriever._hop_distances = lambda aids, bids, h, cfg=None: {}
        nodes = retriever.retrieve("주문 결제", self._cfg(
            cluster_anchor_gap=0.05, cluster_support_policy="graph_gated",
            cluster_direct_gap=0.05, cluster_hop_gate=2,
            cluster_rank_floor=2))
        ids = {n["id"] for n in nodes}
        self.assertIn("b", ids)
        self.assertNotIn("c", ids)
        self.assertEqual({"a"}, set(retriever.call_queries))
        b = next(n for n in nodes if n["id"] == "b")
        self.assertIn("SEMANTIC_SUPPORT", {e["relation"] for e in b["evidence"]})

    @patch("search.ranking.retriever.embedder.encode_texts", return_value=[[1.0]])
    def test_flat_low_scores_always_keep_top1(self, _encode):
        # 하한 1 — 절벽이 없어도 최고점은 항상 앵커(결과 0 위장 금지).
        retriever = _Retriever()
        nodes = retriever.retrieve("주문 결제", self._cfg(
            cluster_anchor_gap=0.0, cluster_support_policy="anchors_only"))
        self.assertEqual(["a"], [n["id"] for n in nodes if n["role"] == "anchor"])


if __name__ == "__main__":
    unittest.main()
