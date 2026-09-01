# -*- coding: utf-8 -*-
"""build_control_inventory 의 선언 상수값 배선 계약 (2026-08-03 실측 갭 수리).

프롬프트는 referenced_symbols 의 immutable_constant 선언값을 2순위 증거로 인정한다 —
채점 앵커(inventory.source_values)도 같은 값을 인정해야 정당 인용이 창작으로
오판되지 않는다. 이 배선이 끊기면 LEN 심볼의 선언값(예: 3)을 쓴 규칙이 거절된다
(z10020 probe 실측).
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from graph.schema.graph_nodes import CodeNode
from pipeline.models.analysis_context import (
    AnalysisContext,
    ReferencedSymbolContext,
    StatementFact,
)
from pipeline.meaning.rule.control_inventory import build_control_inventory
from shared.files.source_cache import SourceFileCache


def _make(symbols):
    tmp = tempfile.TemporaryDirectory()
    source = Path(tmp.name) / "sample.c"
    source.write_text(
        "long fn(void) {\nif (x == LEN_A) {\nreturn 1;\n}\n}\n", encoding="utf-8"
    )
    fn = CodeNode(
        id="fn", signature="fn()", file_path=str(source),
        start_line=1, end_line=5, label="FUNCTION",
    )
    ctx = AnalysisContext(
        id="fn", signature="fn()", file_path=str(source),
        start_line=1, end_line=5,
        referenced_symbols=tuple(symbols),
    )
    # parser AST statement 계약 (spec 016) — step6 이 모든 routine 에 명시 제공한다.
    ctx.extra["ast_statements"] = (
        StatementFact("r3", "RETURN", 3, 3, expression="1"),
    )
    return tmp, fn, ctx


class DeclaredValuesReachInventory(unittest.TestCase):
    def test_immutable_constant_value_joins_source_values(self):
        tmp, fn, ctx = _make([
            ReferencedSymbolContext(
                id="c:LEN_A", name="LEN_A", kind="constant",
                declared_value="3", value_semantics="immutable_constant",
            ),
        ])
        with tmp:
            inventory = build_control_inventory(fn, ctx, SourceFileCache())
        self.assertIn("3", inventory.source_values)

    def test_non_constant_symbols_do_not_leak(self):
        tmp, fn, ctx = _make([
            ReferencedSymbolContext(
                id="c:gv", name="gv", kind="variable",
                declared_value="99", value_semantics="mutable_shared_state",
            ),
        ])
        with tmp:
            inventory = build_control_inventory(fn, ctx, SourceFileCache())
        self.assertNotIn("99", inventory.source_values)


if __name__ == "__main__":
    unittest.main()
