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
from pipeline.models.data_object_usage import (
    DatabaseLinkComponentEvidence,
    DataObjectUsageEvidence,
)


def _routine(node_id: str) -> CodeNode:
    return CodeNode(
        id=node_id, signature=node_id, file_path="sample.sql",
        start_line=1, end_line=9, label="PROCEDURE", name=node_id,
        code_text="\n".join(f"line {line}" for line in range(1, 10)),
        owner_id="sample.sql", parent_id=None,
    )


def _edge(
    relation: str, table: str, dml: str, *, source_id: str = "fn",
    evidence_line: int = 1,
) -> ReadsWrites:
    return ReadsWrites(
        source_id=source_id, target_id=table, relation=relation, dml_type=dml,
        evidence_file="sample.sql", evidence_line=evidence_line,
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

    async def test_child_statement_rw_is_aggregated_to_owning_routine(self):
        routine = _routine("fn")
        select = CodeNode(
            id="select", signature="SELECT", file_path="sample.sql",
            start_line=3, end_line=3, label="SELECT", name="SELECT",
            code_text="SELECT value FROM RDITAG_TB", owner_id="sample.sql",
            parent_id=routine.id,
        )
        select.extra["data_object_evidence_version"] = 3
        select.extra["data_object_usages"] = (DataObjectUsageEvidence(
            source_id=select.id, raw_reference="RDITAG_TB", name="RDITAG_TB",
            access="READ", evidence_file="sample.sql", evidence_line=3,
            table_id="db:RDITAG_TB", resolution="exact",
        ),)
        strategy = SimpleNamespace(
            context_builder=FrameworkContextBuilder(),
            analysis_targets=framework_analysis_targets,
        )
        result = await build_context_step(
            {
                "code_nodes": [routine, select],
                "reads_writes_edges": [
                    _edge(
                        "READS", "db:RDITAG_TB", "SELECT",
                        source_id=routine.id, evidence_line=3,
                    ),
                ],
            },
            {"configurable": {"strategy": strategy}},
        )
        contexts = result["analysis_contexts"]
        self.assertEqual((), contexts[select.id].reads)
        self.assertEqual(("db:RDITAG_TB",), contexts[routine.id].reads)
        self.assertEqual((), contexts[select.id].direct_reads)
        self.assertEqual(("db:RDITAG_TB",), contexts[routine.id].direct_reads)
        self.assertEqual(
            ((3, "select", "RDITAG_TB"),),
            contexts[routine.id].extra["ast_table_ops"],
        )

    async def test_framework_routine_rw_does_not_become_dml_rule_effect(self):
        """루틴에 붙은 framework R/W는 관계 근거이며 AST DML 행이 아니다."""
        routine = _routine("fn")
        strategy = SimpleNamespace(
            context_builder=FrameworkContextBuilder(),
            analysis_targets=framework_analysis_targets,
        )
        result = await build_context_step(
            {
                "code_nodes": [routine],
                "reads_writes_edges": [
                    _edge("READS", "db:COMM_CODE", "SELECT", evidence_line=6),
                ],
            },
            {"configurable": {"strategy": strategy}},
        )
        context = result["analysis_contexts"][routine.id]
        self.assertEqual(("db:COMM_CODE",), context.reads)
        self.assertNotIn("ast_table_ops", context.extra)
        usages = context.data_object_usages
        self.assertEqual(1, len(usages))
        self.assertEqual(
            ("db:COMM_CODE", "READ", "linked_edges", "exact"),
            (usages[0].table_id, usages[0].access, usages[0].producer, usages[0].resolution),
        )
        self.assertEqual(6, usages[0].evidence_line)

    async def test_dml_evidence_outside_owner_fails_loudly(self):
        routine = _routine("fn")
        select = CodeNode(
            id="select", signature="SELECT", file_path="sample.sql",
            start_line=3, end_line=3, label="SELECT", name="SELECT",
            code_text="SELECT value FROM T", owner_id="sample.sql",
            parent_id=routine.id,
        )
        select.extra["data_object_evidence_version"] = 3
        select.extra["data_object_usages"] = (DataObjectUsageEvidence(
            source_id=select.id, raw_reference="T", name="T", access="READ",
            evidence_file="sample.sql", evidence_line=20,
        ),)
        strategy = SimpleNamespace(
            context_builder=FrameworkContextBuilder(),
            analysis_targets=framework_analysis_targets,
        )
        with self.assertRaisesRegex(ValueError, "소유 routine 범위 밖"):
            await build_context_step(
                {
                    "code_nodes": [routine, select],
                    "reads_writes_edges": [],
                },
                {"configurable": {"strategy": strategy}},
            )

    async def test_unresolved_usage_keeps_raw_rule_evidence_without_table_alias(self):
        routine = _routine("fn")
        insert = CodeNode(
            id="insert", signature="INSERT", file_path="sample.sql",
            start_line=3, end_line=3, label="INSERT", name="INSERT",
            code_text="INSERT INTO ORDERS@REMOTE VALUES (1)", owner_id="sample.sql",
            parent_id=routine.id,
        )
        usage = DataObjectUsageEvidence(
            source_id=insert.id, raw_reference="ORDERS@REMOTE", name="ORDERS",
            database_link="REMOTE", database_link_components=(
                DatabaseLinkComponentEvidence(role="database", name="REMOTE"),
            ), access="WRITE", evidence_file="sample.sql",
            evidence_line=3, datasource="shop",
        )
        insert.extra["data_object_evidence_version"] = 3
        insert.extra["data_object_usages"] = (usage,)
        strategy = SimpleNamespace(
            context_builder=FrameworkContextBuilder(),
            analysis_targets=framework_analysis_targets,
        )

        result = await build_context_step(
            {
                "code_nodes": [routine, insert],
                "reads_writes_edges": [],
            },
            {"configurable": {
                "strategy": strategy,
                "codebase": SimpleNamespace(datasource="shop"),
            }},
        )

        routine_context = result["analysis_contexts"][routine.id]
        self.assertEqual(
            ((3, "insert", "ORDERS@REMOTE"),),
            routine_context.extra["ast_table_ops"],
        )
        self.assertNotIn("table_identity_aliases", routine_context.extra)
        self.assertEqual((), routine_context.reads)
        self.assertEqual((), routine_context.writes)


if __name__ == "__main__":
    unittest.main()
