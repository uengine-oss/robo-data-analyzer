"""DBMS 테이블 링킹 계약 — 자기 줄 스캔 결과와 소스 캐시 의존성.

`_link` 는 파일 본문을 읽어야 하므로 소스 캐시가 필요하다. 그 의존은 호출자가
명시적으로 넘긴다 — run 스코프 캐시 공유(spec 046 FR-008)가 호출 경로에 드러나야
하고, 숨은 스코프 조회로 대체되면 정적 검사를 통과한 채 런타임에만 깨진다.
"""
from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from graph.schema.graph_nodes import CodeNode, Table
from shared.files.cache import SourceFileCache
from pipeline.steps.step5_linking.link_tables.link_dbms_tables_step import _link


def _procedure(node_id: str, file_path: str, start_line: int, end_line: int) -> CodeNode:
    return CodeNode(
        id=node_id,
        signature=node_id,
        file_path=file_path,
        start_line=start_line,
        end_line=end_line,
        label="PROCEDURE",
        name=node_id,
    )


class LinkDbmsTablesTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.source = Path(self._tmp.name) / "load_orders.sql"
        self.source.write_text(
            "PROCEDURE load_orders IS\n"
            "BEGIN\n"
            "  INSERT INTO ORDER_ITEM SELECT * FROM ORDERS;\n"
            "END;\n",
            encoding="utf-8",
        )
        self.tables = [
            Table(db="shop", schema="PUBLIC", name="ORDERS"),
            Table(db="shop", schema="PUBLIC", name="ORDER_ITEM"),
        ]
        self.nodes = [_procedure("proc:load_orders", str(self.source), 1, 4)]

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
        self.assertEqual(cache.lines(str(self.source))[2].strip().split()[0], "INSERT")

    def test_no_ddl_tables_returns_no_edges(self) -> None:
        """경계: DDL 테이블이 없으면 빈 결과."""
        self.assertEqual(_link(self.nodes, [], [], SourceFileCache()), [])


if __name__ == "__main__":
    unittest.main()
