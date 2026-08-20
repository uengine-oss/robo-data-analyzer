"""DBMS 테이블 링킹 계약 — 자기 줄 스캔 결과와 소스 캐시 의존성.

`_link` 는 파일 본문을 읽어야 하므로 소스 캐시가 필요하다. 그 의존은 호출자가
명시적으로 넘긴다 — run 스코프 캐시 공유(spec 046 FR-008)가 호출 경로에 드러나야
하고, 숨은 스코프 조회로 대체되면 정적 검사를 통과한 채 런타임에만 깨진다.
"""
from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from graph.schema.graph_edges import ParentOf
from graph.schema.graph_nodes import CodeNode, Table
from shared.files.cache import SourceFileCache
from pipeline.steps.step5_linking.link_tables.link_dbms_tables_step import (
    _link,
    _unresolved_object_references,
)
from pipeline.models.data_object_usage import (
    DatabaseLinkComponentEvidence,
    DataObjectUsageEvidence,
    QualifiedColumnUsage,
)


def _statement(node_id: str, file_path: str, start_line: int, end_line: int) -> CodeNode:
    return CodeNode(
        id=node_id,
        signature=node_id,
        file_path=file_path,
        start_line=start_line,
        end_line=end_line,
        label="INSERT",
        name=node_id,
    )


class LinkDbmsTablesTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.source = Path(self._tmp.name) / "load_orders.sql"
        self.source.write_text(
            "INSERT INTO ORDER_ITEM SELECT * FROM ORDERS;\n",
            encoding="utf-8",
        )
        self.tables = [
            Table(db="shop", schema="PUBLIC", name="ORDERS"),
            Table(db="shop", schema="PUBLIC", name="ORDER_ITEM"),
        ]
        self.nodes = [_statement("insert:load_orders", str(self.source), 1, 1)]

    def test_links_reads_and_writes_from_own_lines(self) -> None:
        """정상: 자기 줄의 INSERT 대상은 WRITES, FROM 대상은 READS."""
        edges = _link(self.nodes, self.tables, [], SourceFileCache())

        relation_by_target = {e.target_id: e.relation for e in edges}
        orders, order_item = self.tables[0].id, self.tables[1].id
        self.assertEqual(relation_by_target.get(order_item), "WRITES")
        self.assertEqual(relation_by_target.get(orders), "READS")

    def test_reuses_the_caller_supplied_cache(self) -> None:
        """캐시는 호출자가 넘긴 인스턴스를 그대로 쓴다 — run 스코프 공유가 깨지지 않는다."""
        cache = SourceFileCache()

        _link(self.nodes, self.tables, [], cache)

        # 넘긴 캐시에 이 파일이 적재됐어야 한다 = 별도 캐시를 새로 만들지 않았다는 증거.
        self.assertEqual(cache.lines(str(self.source))[0].strip().split()[0], "INSERT")

    def test_no_ddl_tables_returns_no_edges(self) -> None:
        """경계: DDL 테이블이 없으면 빈 결과."""
        self.assertEqual(_link(self.nodes, [], [], SourceFileCache()), [])

    def test_no_ddl_tables_preserves_versioned_reference_as_object_reference_edge(self) -> None:
        node = self.nodes[0]
        node.extra["data_object_evidence_version"] = 3
        node.extra["data_object_usages"] = (DataObjectUsageEvidence(
            source_id=node.id, raw_reference="ORDERS@REMOTE", name="ORDERS",
            database_link="REMOTE", database_link_components=(
                DatabaseLinkComponentEvidence(role="database", name="REMOTE"),
            ), access="READ", evidence_file=str(self.source),
            evidence_line=1,
        ),)

        edges = _link([node], [], [], SourceFileCache(), datasource="shop")
        references = _unresolved_object_references([node], "shop")

        self.assertEqual(len(edges), 1)
        self.assertEqual(edges[0].relation, "READS")
        self.assertTrue(edges[0].target_id.startswith("object-ref:"))
        self.assertEqual(
            references[0].database_link_components_json,
            '[{"role":"database","name":"REMOTE","name_quoted":false}]',
        )

    def test_quoted_unresolved_siblings_remain_two_edges_and_two_nodes(self) -> None:
        node = self.nodes[0]
        node.extra["data_object_evidence_version"] = 3
        node.extra["data_object_usages"] = tuple(
            DataObjectUsageEvidence(
                source_id=node.id, raw_reference=f'"App"."{name}"', schema="App",
                schema_quoted=True, name=name, name_quoted=True, access="READ",
                evidence_file=str(self.source), evidence_line=line,
            )
            for name, line in (("Foo", 1), ("foo", 2))
        )

        edges = _link([node], [], [], SourceFileCache(), datasource="shop")
        references = _unresolved_object_references([node], "shop")

        self.assertEqual(len(edges), 2)
        self.assertEqual(len({edge.target_id for edge in edges}), 2)
        self.assertEqual(len(references), 2)
        self.assertEqual(
            {(reference.schema, reference.schema_quoted,
              reference.name, reference.name_quoted) for reference in references},
            {("App", True, "Foo", True), ("App", True, "foo", True)},
        )

    def test_versioned_ast_evidence_resolves_schema_and_preserves_alias_columns(self) -> None:
        node = self.nodes[0]
        node.extra["data_object_evidence_version"] = 1
        node.extra["data_object_usages"] = (
            DataObjectUsageEvidence(
                source_id=node.id, raw_reference="PUBLIC.ORDERS", schema="PUBLIC",
                name="ORDERS", alias="O", access="READ", evidence_file=str(self.source),
                evidence_line=1, qualified_columns=(QualifiedColumnUsage(
                    raw_reference="O.ORDER_ID", qualifier="O", name="ORDER_ID", evidence_line=1,
                ),),
            ),
            DataObjectUsageEvidence(
                source_id=node.id, raw_reference="PUBLIC.ORDER_ITEM", schema="PUBLIC",
                name="ORDER_ITEM", alias="I", access="WRITE", evidence_file=str(self.source),
                evidence_line=1,
            ),
        )

        edges = _link([node], self.tables, [], SourceFileCache(), datasource="shop")

        self.assertEqual(
            [(edge.target_id, edge.relation) for edge in edges],
            [(self.tables[0].id, "READS"), (self.tables[1].id, "WRITES")],
        )
        usages = node.extra["data_object_usages"]
        self.assertEqual([usage.resolution for usage in usages], ["exact", "exact"])
        self.assertEqual([usage.datasource for usage in usages], ["shop", "shop"])
        self.assertEqual(usages[0].qualified_columns[0].name, "ORDER_ID")

    def test_versioned_ast_evidence_resolves_only_exact_quoted_table_identity(self) -> None:
        node = self.nodes[0]
        node.extra["data_object_evidence_version"] = 3
        node.extra["data_object_usages"] = (DataObjectUsageEvidence(
            source_id=node.id, raw_reference='"App"."foo"', schema="App",
            schema_quoted=True, name="foo", name_quoted=True, access="READ",
            evidence_file=str(self.source), evidence_line=1,
        ),)
        tables = [
            Table(db="shop", schema="App", schema_quoted=True,
                  name="Foo", name_quoted=True),
            Table(db="shop", schema="App", schema_quoted=True,
                  name="foo", name_quoted=True),
        ]

        edges = _link([node], tables, [], SourceFileCache(), datasource="shop")

        self.assertEqual([(tables[1].id, "READS")], [
            (edge.target_id, edge.relation) for edge in edges
        ])

    def test_legacy_bare_name_scanner_does_not_guess_between_schema_siblings(self) -> None:
        tables = [
            Table(db="shop", schema="PUBLIC", name="ORDERS"),
            Table(db="shop", schema="TENANT", name="ORDERS"),
        ]

        self.assertEqual(
            [],
            _link(self.nodes, tables, [], SourceFileCache(), datasource="shop"),
        )

    def test_versioned_evidence_does_not_guess_ambiguous_or_database_linked_objects(self) -> None:
        node = self.nodes[0]
        node.extra["data_object_evidence_version"] = 1
        node.extra["data_object_usages"] = (
            DataObjectUsageEvidence(
                source_id=node.id, raw_reference="ORDERS", name="ORDERS", access="READ",
                evidence_file=str(self.source), evidence_line=1,
            ),
            DataObjectUsageEvidence(
                source_id=node.id, raw_reference="ORDER_ITEM@REMOTE", name="ORDER_ITEM",
                database_link="REMOTE", database_link_components=(
                    DatabaseLinkComponentEvidence(role="database", name="REMOTE"),
                ), access="WRITE", evidence_file=str(self.source),
                evidence_line=1,
            ),
        )
        ambiguous = self.tables + [Table(db="shop", schema="TENANT2", name="ORDERS")]

        edges = _link([node], ambiguous, [], SourceFileCache())

        self.assertEqual(len(edges), 2)
        self.assertTrue(all(edge.target_id.startswith("object-ref:") for edge in edges))
        self.assertEqual(
            [usage.raw_reference for usage in node.extra["unresolved_data_object_usages"]],
            ["ORDERS", "ORDER_ITEM@REMOTE"],
        )

    def test_trigger_event_header_is_not_an_executable_insert(self) -> None:
        """트리거 선언의 AFTER INSERT ON 대상은 READS가 아니다."""
        self.source.write_text(
            "CREATE OR REPLACE TRIGGER RWIS.TRG_INS_RDITAG_TB\n"
            "AFTER INSERT\n"
            "ON RDITAG_TB\n"
            "FOR EACH ROW\n"
            "BEGIN\n"
            "  INSERT INTO TMP_INS_TRAN2FA_RDITAG_TB VALUES (:NEW.TAGSN);\n"
            "END;\n",
            encoding="utf-8",
        )
        trigger = CodeNode(
            id="trigger:rditag",
            signature="TRG_INS_RDITAG_TB",
            file_path=str(self.source),
            start_line=1,
            end_line=7,
            label="TRIGGER",
            name="TRG_INS_RDITAG_TB",
        )
        insert = CodeNode(
            id="trigger:rditag:insert@6",
            signature="INSERT",
            file_path=str(self.source),
            start_line=6,
            end_line=6,
            label="INSERT",
            name="INSERT",
        )
        tables = [
            Table(db="rwis", schema="RWIS", name="RDITAG_TB"),
            Table(db="rwis", schema="RWIS", name="TMP_INS_TRAN2FA_RDITAG_TB"),
        ]

        edges = _link(
            [trigger, insert],
            tables,
            [ParentOf(parent_id=trigger.id, child_id=insert.id)],
            SourceFileCache(),
        )

        self.assertEqual(
            [(edge.source_id, edge.target_id, edge.relation, edge.dml_type)
             for edge in edges],
            [(insert.id, tables[1].id, "WRITES", "INSERT")],
        )

    def test_routine_container_is_not_a_dml_fallback(self) -> None:
        """하위 DML 누락을 루틴 재스캔으로 숨기지 않는다 — parser 계약 위반은 별도 축이다."""
        self.source.write_text(
            "CREATE OR REPLACE PROCEDURE P IS\n"
            "BEGIN\n"
            "  INSERT INTO ORDER_ITEM SELECT * FROM ORDERS;\n"
            "END;\n",
            encoding="utf-8",
        )
        routine = CodeNode(
            id="procedure:p",
            signature="P",
            file_path=str(self.source),
            start_line=1,
            end_line=4,
            label="PROCEDURE",
            name="P",
        )

        self.assertEqual(
            _link([routine], self.tables, [], SourceFileCache()),
            [],
        )


if __name__ == "__main__":
    unittest.main()
