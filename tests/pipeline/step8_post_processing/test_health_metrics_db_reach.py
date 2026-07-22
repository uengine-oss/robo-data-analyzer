# -*- coding: utf-8 -*-
"""spec 079 — health_metrics 의 DB 도달성(db_reach) 지표 단위 테스트.

계약 (spec 079 FR-1~FR-3):
- 시드: R/W 엣지를 가진 함수.
- 전파: internal 호출의 역방향 — 시드를 (몇 단계든) 호출하는 함수 전부.
- 미도달 함수는 db_reach=False (저장 시 속성 부재).
- 순환이 있어도 종료하며, 같은 입력이면 같은 결과(결정론).
"""
import unittest

from graph.schema.graph_edges import Calls, ReadsWrites
from graph.schema.graph_nodes import CodeNode
from pipeline.steps.step8_post_processing.health_metrics_step import _compute_metrics


def _fn(fid: str) -> CodeNode:
    return CodeNode(
        id=fid, signature=f"{fid}()", file_path="sample.c",
        start_line=1, end_line=10, label="FUNCTION", name=fid,
    )


def _call(caller: str, callee: str) -> Calls:
    return Calls(caller_id=caller, callee_id=callee, scope="internal")


def _rw(source: str, table: str) -> ReadsWrites:
    return ReadsWrites(source_id=source, target_id=table, relation="READS")


class DbReachTest(unittest.TestCase):

    def _reach_map(self, nodes, calls, rws) -> dict[str, bool]:
        items = _compute_metrics(nodes, calls, rws)
        return {it["id"]: it["db_reach"] for it in items}

    def test_seed_and_ancestors_are_reached_others_not(self):
        # E → A → B → C(R/W) , D 는 고립.
        nodes = [_fn(x) for x in ("A", "B", "C", "D", "E")]
        calls = [_call("E", "A"), _call("A", "B"), _call("B", "C")]
        rws = [_rw("C", "T1")]
        reach = self._reach_map(nodes, calls, rws)
        self.assertTrue(reach["C"], "R/W 보유 함수는 직접 시드")
        self.assertTrue(reach["B"], "시드를 직접 호출하는 함수는 도달")
        self.assertTrue(reach["A"], "2단계 조상도 도달")
        self.assertTrue(reach["E"], "3단계 조상도 도달")
        self.assertFalse(reach["D"], "사슬 밖 함수는 미도달")

    def test_cycle_terminates_and_reaches_all_members(self):
        # A ↔ B 순환 + B → C(R/W).
        nodes = [_fn(x) for x in ("A", "B", "C")]
        calls = [_call("A", "B"), _call("B", "A"), _call("B", "C")]
        rws = [_rw("C", "T1")]
        reach = self._reach_map(nodes, calls, rws)
        self.assertTrue(reach["A"] and reach["B"] and reach["C"])

    def test_no_rw_means_no_reach_anywhere(self):
        nodes = [_fn(x) for x in ("A", "B")]
        calls = [_call("A", "B")]
        reach = self._reach_map(nodes, calls, [])
        self.assertFalse(any(reach.values()), "시드가 없으면 아무도 도달하지 않는다")

    def test_deterministic_same_input_same_output(self):
        nodes = [_fn(x) for x in ("A", "B", "C")]
        calls = [_call("A", "B"), _call("B", "C")]
        rws = [_rw("C", "T1")]
        first = self._reach_map(nodes, calls, rws)
        second = self._reach_map(nodes, calls, rws)
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
