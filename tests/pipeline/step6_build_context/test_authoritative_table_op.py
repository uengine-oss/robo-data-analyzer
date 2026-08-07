"""권위 table op 파생 계약 — 관계가 op 를 결정한다.

엣지의 ``dml_type`` 은 그 구문의 사실이다(예: INSERT…SELECT 문 안의 읽기 테이블,
트리거 헤더 ``AFTER INSERT ON T`` 의 대상 테이블). 그러나 **테이블의 권위 op** 는
관계가 결정한다 — READS 엣지는 READ 를, WRITES 엣지만 자기 dml 을 기여한다.

실측 회귀(2026-08-07 rwis): 트리거 대상 테이블의 READS 엣지에 구문 dml(INSERT)이
찍혔고, 소비자가 이를 테이블 op 로 승격해 "INSERT requires WRITE ownership" fatal.
"""
from __future__ import annotations

import unittest
from types import SimpleNamespace

from graph.schema.graph_edges import ReadsWrites
from graph.schema.graph_nodes import CodeNode
from pipeline.steps.step6_build_context.build_context_step import build_context_step
from pipeline.steps.step6_build_context.framework_context_builder import (
    FrameworkContextBuilder,
)
from pipeline.steps.step7_analyze_code.framework_per_node import (
    framework_analysis_targets,
)


def _routine(node_id: str) -> CodeNode:
    return CodeNode(
        id=node_id, signature=node_id, file_path="sample.sql",
        start_line=1, end_line=9, label="PROCEDURE", name=node_id,
        code_text="\n".join(f"line {line}" for line in range(1, 10)),
        owner_id="sample.sql", parent_id=None,
    )


def _edge(relation: str, table: str, dml: str) -> ReadsWrites:
    return ReadsWrites(
        source_id="fn", target_id=table, relation=relation, dml_type=dml,
        evidence_file="sample.sql", evidence_line=1,
    )


class AuthoritativeTableOpTest(unittest.IsolatedAsyncioTestCase):
    async def _table_op(self, edges: list[ReadsWrites]) -> dict:
        strategy = SimpleNamespace(
            context_builder=FrameworkContextBuilder(),
            analysis_targets=framework_analysis_targets,
        )
        result = await build_context_step(
            {"code_nodes": [_routine("fn")], "reads_writes_edges": edges},
            {"configurable": {"strategy": strategy}},
        )
        return result["analysis_contexts"]["fn"].extra.get("table_op") or {}

    async def test_read_edge_with_statement_dml_yields_read_op(self):
        """트리거 헤더·INSERT…SELECT 의 읽기 테이블 — 구문 dml 이 op 로 새면 안 된다."""
        table_op = await self._table_op([_edge("READS", "db:S.T", "INSERT")])
        self.assertEqual({"db:S.T": "READ"}, table_op)

    async def test_write_edge_contributes_its_dml(self):
        table_op = await self._table_op([_edge("WRITES", "db:S.T", "INSERT")])
        self.assertEqual({"db:S.T": "INSERT"}, table_op)

    async def test_select_read_still_normalizes_to_read(self):
        table_op = await self._table_op([_edge("READS", "db:S.T", "SELECT")])
        self.assertEqual({"db:S.T": "READ"}, table_op)

    async def test_read_and_write_of_same_table_is_single_write_op(self):
        """READ 는 관계 사실이고 WRITE dml 이 유일하면 그 op 가 권위다 (UPDATE 후 재조회 등)."""
        table_op = await self._table_op([
            _edge("READS", "db:S.T", "SELECT"),
            _edge("WRITES", "db:S.T", "UPDATE"),
        ])
        self.assertEqual({"db:S.T": "UPDATE"}, table_op)

    async def test_conflicting_write_dmls_are_excluded_as_ambiguous(self):
        table_op = await self._table_op([
            _edge("WRITES", "db:S.T", "INSERT"),
            _edge("WRITES", "db:S.T", "UPDATE"),
        ])
        self.assertEqual({}, table_op)


if __name__ == "__main__":
    unittest.main()
