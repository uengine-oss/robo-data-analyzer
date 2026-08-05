"""spec 111 — 상속 조상 필드/상수 매칭 (REFERENCES/INIT_BY).

Java extends · Python bases · 자기 모듈 우선 섀도잉 · 복수 후보 포기(과대근사 금지)를
고정한다. 선언된 필드(self.cfg 의 cfg)가 종전에도 잡히던 동작은 회귀로 함께 고정.
"""
from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from graph.schema.graph_nodes import CodeNode, Constant, Module, Variable
from pipeline.steps.step2_load_ast.token_matcher import link_references


def _module(mid: str, name: str, path: str, start: int) -> Module:
    return Module(id=mid, name=name, file_path=path, label="CLASS", start_line=start)


def _fn(fid: str, owner: str, path: str, start: int, end: int) -> CodeNode:
    return CodeNode(id=fid, signature=fid, file_path=path,
                    start_line=start, end_line=end, label="METHOD", owner_id=owner)


def _const(cid: str, owner: str, name: str, path: str, value: str = "'x'") -> Constant:
    return Constant(id=cid, owner_id=owner, name=name, file_path=path,
                    start_line=1, end_line=1, label="FIELD", value=value)


class JavaInheritanceTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)

    async def test_extends_constant_is_referenced(self):
        """자식 메서드가 부모 상수를 맨이름으로 참조 → REFERENCES 생성 (종전 미탐)."""
        src = self.dir / "Order.java"
        src.write_text(
            "class Base {\n"                       # 1
            "  static final int MAX_QTY = 10;\n"   # 2
            "}\n"                                   # 3
            "class Order extends Base {\n"          # 4
            "  void check() {\n"                    # 5
            "    if (qty > MAX_QTY) reject();\n"    # 6
            "  }\n"
            "}\n",
            encoding="utf-8",
        )
        p = str(src)
        refs, _ = await link_references(
            code_nodes=[_fn("Order.check", "Order", p, 5, 7)],
            variables=[],
            constants=[_const("Base.MAX_QTY", "Base", "MAX_QTY", p)],
            import_refs=[],
            modules=[_module("Base", "Base", p, 1), _module("Order", "Order", p, 4)],
        )
        self.assertEqual([(r.source_id, r.target_id) for r in refs],
                         [("Order.check", "Base.MAX_QTY")])

    async def test_own_field_shadows_parent(self):
        """자기 모듈에 같은 이름 → 조상 매칭 건너뜀 (섀도잉)."""
        src = self.dir / "Order.java"
        src.write_text(
            "class Base {\n  static final int MAX = 1;\n}\n"
            "class Order extends Base {\n"
            "  static final int MAX = 2;\n"
            "  void f() { use(MAX); }\n"
            "}\n",
            encoding="utf-8",
        )
        p = str(src)
        refs, _ = await link_references(
            code_nodes=[_fn("Order.f", "Order", p, 6, 6)],
            variables=[],
            constants=[_const("Base.MAX", "Base", "MAX", p),
                       _const("Order.MAX", "Order", "MAX", p)],
            import_refs=[],
            modules=[_module("Base", "Base", p, 1), _module("Order", "Order", p, 4)],
        )
        self.assertEqual({r.target_id for r in refs}, {"Order.MAX"})   # 부모 것 아님

    async def test_python_base_class_constant(self):
        src = self.dir / "svc.py"
        src.write_text(
            "class Base:\n"                    # 1
            "    DEFAULT_CFG = {'a': 1}\n"     # 2
            "class Svc(Base):\n"               # 3
            "    def run(self):\n"             # 4
            "        use(DEFAULT_CFG)\n",      # 5
            encoding="utf-8",
        )
        p = str(src)
        refs, _ = await link_references(
            code_nodes=[_fn("Svc.run", "Svc", p, 4, 5)],
            variables=[],
            constants=[_const("Base.DEFAULT_CFG", "Base", "DEFAULT_CFG", p)],
            import_refs=[],
            modules=[_module("Base", "Base", p, 1), _module("Svc", "Svc", p, 3)],
        )
        self.assertEqual([r.target_id for r in refs], ["Base.DEFAULT_CFG"])

    async def test_declared_own_field_regression(self):
        """회귀 고정 — 자기 클래스에 선언된 필드는 종전대로 잡힌다 (self.cfg 의 cfg)."""
        src = self.dir / "svc.py"
        src.write_text(
            "class Svc:\n"
            "    cfg = None\n"
            "    def run(self):\n"
            "        use(self.cfg)\n",
            encoding="utf-8",
        )
        p = str(src)
        refs, _ = await link_references(
            code_nodes=[_fn("Svc.run", "Svc", p, 3, 4)],
            variables=[Variable(id="Svc.cfg", owner_id="Svc", name="cfg", file_path=p,
                                start_line=2, end_line=2, label="FIELD")],
            constants=[],
            import_refs=[],
            modules=[_module("Svc", "Svc", p, 1)],
        )
        self.assertIn("Svc.cfg", {r.target_id for r in refs})

    async def test_ambiguous_parent_is_abandoned(self):
        """같은 파일에 동명 부모 2개(중의) → 상속 매칭 포기 (과대근사 금지)."""
        src = self.dir / "x.java"
        src.write_text(
            "class Base {\n  static final int K = 1;\n}\n"
            "class Base {\n  static final int K = 2;\n}\n"
            "class C extends Base {\n  void f() { use(K); }\n}\n",
            encoding="utf-8",
        )
        p = str(src)
        refs, _ = await link_references(
            code_nodes=[_fn("C.f", "C", p, 7, 7)],
            variables=[],
            constants=[_const("Base1.K", "Base1", "K", p), _const("Base2.K", "Base2", "K", p)],
            import_refs=[],
            modules=[_module("Base1", "Base", p, 1), _module("Base2", "Base", p, 4),
                     _module("C", "C", p, 6)],
        )
        self.assertEqual(refs, [])


if __name__ == "__main__":
    unittest.main()
