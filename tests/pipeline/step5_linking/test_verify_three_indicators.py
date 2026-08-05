"""spec 114 — 검문소 3지표 (support/contradiction/ambiguity).

현행 구멍의 재현을 그대로 고정한다: "넓게 오탐하는 규칙이 운 좋은 엣지 1개로 통과".
"""
from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from agents.contracts import DdlTransform, EmbeddedSqlRule, OpMapping, TableAccessPattern
from graph.schema.graph_nodes import CodeNode, Table
from pipeline.steps.step5_linking.link_tables.regex_table_linker import (
    _resolve_table_hits,
    verify_table_patterns,
)


def _node(path: str, n_lines: int) -> CodeNode:
    return CodeNode(id="f:1", signature="f", file_path=path,
                    start_line=1, end_line=n_lines, label="FUNCTION")


def _pattern(regex: str) -> TableAccessPattern:
    return TableAccessPattern(
        id="p", call_regex=regex,
        op_mappings=[OpMapping(token="FROM", op="SELECT")],
    )


class ThreeIndicatorGateTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.transform = DdlTransform(ddl_naming_rule="as_is")

    async def _verify(self, source: str, tables: list[str], regex: str):
        src = self.root / "a.c"
        src.write_text(source, encoding="utf-8")
        return await verify_table_patterns(
            patterns=[_pattern(regex)],
            ddl_transform=self.transform,
            project_root=self.root,
            code_nodes=[_node(str(src), source.count("\n") + 1)],
            tables=[Table(db="d", schema="S", name=n) for n in tables],
        )

    async def test_lucky_single_edge_with_mass_contradiction_is_rejected(self):
        """구멍 재현 — 매치 다수·진짜 1건. 종전엔 통과, 3지표로 탈락."""
        source = "\n".join(
            ['x("SELECT A FROM ORDERS");']                       # 진짜 1건
            + [f'log("read FROM garbage_{i}");' for i in range(9)]   # 단어장 밖 9건
        )
        verdict = await self._verify(
            source, ["ORDERS"],
            r'(?P<op_token>FROM)\s+(?P<entity>[A-Za-z_][A-Za-z0-9_]*)',
        )
        self.assertFalse(verdict.ok)
        self.assertIn("모순 90%", verdict.summary)

    async def test_precise_rule_still_passes(self):
        """회귀 — 정밀한 규칙(모순 0)은 종전대로 통과."""
        source = 'x("SELECT A FROM ORDERS");\ny("SELECT B FROM USERS");'
        verdict = await self._verify(
            source, ["ORDERS", "USERS"],
            r'(?P<op_token>FROM)\s+(?P<entity>[A-Za-z_][A-Za-z0-9_]*)',
        )
        self.assertTrue(verdict.ok)
        self.assertIn("모순 0%", verdict.summary)

    async def test_ambiguous_resolution_is_counted_and_gated(self):
        """fallback prefix 로 T_USER·TB_USER 둘 다 존재 → 중의 계수 + 탈락."""
        src = self.root / "a.c"
        src.write_text('x("SELECT A FROM USER");', encoding="utf-8")
        verdict = await verify_table_patterns(
            patterns=[_pattern(r'(?P<op_token>FROM)\s+(?P<entity>[A-Za-z_][A-Za-z0-9_]*)')],
            ddl_transform=DdlTransform(
                ddl_naming_rule="as_is", fallback_prefixes=["T_", "TB_"],
            ),
            project_root=self.root,
            code_nodes=[_node(str(src), 1)],
            tables=[Table(db="d", schema="S", name=n) for n in ("T_USER", "TB_USER")],
        )
        self.assertFalse(verdict.ok)                 # 중의 100% > 상한 20%
        self.assertIn("중의 100%", verdict.summary)

    async def test_dead_pattern_cannot_hide_behind_verified_sibling(self):
        src = self.root / "a.c"
        src.write_text('x("SELECT A FROM ORDERS");', encoding="utf-8")
        good = _pattern(r'(?P<op_token>FROM)\s+(?P<entity>[A-Za-z_][A-Za-z0-9_]*)')
        dead = TableAccessPattern(
            id="dead",
            call_regex=r'NEVER_(?P<op_token>SELECT)_(?P<entity>[A-Za-z_][A-Za-z0-9_]*)',
            op_mappings=[OpMapping(token="SELECT", op="SELECT")],
        )

        verdict = await verify_table_patterns(
            patterns=[good, dead],
            ddl_transform=self.transform,
            project_root=self.root,
            code_nodes=[_node(str(src), 1)],
            tables=[Table(db="d", schema="S", name="ORDERS")],
        )

        self.assertFalse(verdict.ok)
        self.assertEqual(("dead",), verdict.failed_rule_ids)
        self.assertIn("dead(매치 0", verdict.summary)

    async def test_duplicate_rule_ids_are_rejected(self):
        src = self.root / "a.c"
        src.write_text('x("SELECT A FROM ORDERS");', encoding="utf-8")
        duplicate = EmbeddedSqlRule(
            id="p", delimiter_start="EXEC SQL", delimiter_end=";",
        )

        with self.assertRaisesRegex(ValueError, "duplicate table access rule ids: p"):
            await verify_table_patterns(
                patterns=[_pattern(r'(?P<op_token>FROM)\s+(?P<entity>[A-Za-z_][A-Za-z0-9_]*)')],
                embedded_rules=[duplicate],
                ddl_transform=self.transform,
                project_root=self.root,
                code_nodes=[_node(str(src), 1)],
                tables=[Table(db="d", schema="S", name="ORDERS")],
            )


class ResolveHitsTest(unittest.TestCase):
    def test_multiple_hits_preserve_order_first_is_legacy_choice(self):
        lookup = {"T_USER": "db:d.S.T_USER", "TB_USER": "db:d.S.TB_USER"}
        transform = DdlTransform(ddl_naming_rule="as_is", fallback_prefixes=["T_", "TB_"])
        hits = _resolve_table_hits("USER", lookup, transform)
        self.assertEqual(hits, ["db:d.S.T_USER", "db:d.S.TB_USER"])  # 첫 해석 = 종전 선택


if __name__ == "__main__":
    unittest.main()
