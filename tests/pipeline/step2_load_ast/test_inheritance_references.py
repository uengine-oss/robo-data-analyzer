"""spec 111 — 상속 조상 필드/상수 매칭 (REFERENCES/INIT_BY).

Java extends · Python bases · 자기 모듈 우선 섀도잉 · 복수 후보 포기(과대근사 금지)를
고정한다. 선언된 필드(self.cfg 의 cfg)가 종전에도 잡히던 동작은 회귀로 함께 고정.
"""
from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from graph.schema.graph_nodes import CodeNode, Constant, Module, Variable
from graph.nodes.module import to_item as module_to_item
from pipeline.models.analysis_context import AnalysisContext, dump_referenced_symbols
from pipeline.steps.step2_load_ast.node_builders import _make_module
from pipeline.steps.step2_load_ast.token_matcher import link_references
from pipeline.steps.step6_build_context.referenced_symbols import _build_reference_context
from pipeline.steps.step7_analyze_code.semantic_frame_planner import build_code_semantic_plan


def _module(
    mid: str,
    name: str,
    path: str,
    start: int,
    *,
    extends_type: str = "",
    implements_types: str = "",
) -> Module:
    fields = {}
    if extends_type:
        fields["extends_type"] = extends_type
    if implements_types:
        fields["implements_types"] = implements_types
    return Module(
        id=mid, name=name, file_path=path, label="CLASS", start_line=start,
        **fields,
    )


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

    def test_parser_owned_inheritance_fields_survive_ast_module_conversion(self):
        module = _make_module(
            {
                "type": "CLASS",
                "extendsType": "BaseService<Order, Item>",
                "implementsTypes": "OrderPort, Audited",
            },
            "OrderService",
            "OrderService",
            "OrderService.java",
            1,
            9,
        )

        self.assertEqual("BaseService<Order, Item>", module.extends_type)
        self.assertEqual("OrderPort, Audited", module.implements_types)
        self.assertEqual("BaseService<Order, Item>", module_to_item(module)["extends_type"])
        self.assertEqual("OrderPort, Audited", module_to_item(module)["implements_types"])

    async def test_listener_ast_closes_ancestor_beyond_three_hops_without_source_reparse(self):
        """Listener-owned parent fields are authoritative and the finite graph has no depth cap."""
        src = self.dir / "Deep.java"
        src.write_text(
            "class Base { static final int ROOT_LIMIT = 10; }\n"
            "class Level1 {}\n"
            "class Level2 {}\n"
            "class Level3 {}\n"
            "class Level4 { void check() { use(ROOT_LIMIT); } }\n",
            encoding="utf-8",
        )
        p = str(src)
        refs, _ = await link_references(
            code_nodes=[_fn("Level4.check", "Level4", p, 5, 5)],
            variables=[],
            constants=[_const("Base.ROOT_LIMIT", "Base", "ROOT_LIMIT", p)],
            import_refs=[],
            modules=[
                _module("Base", "Base", p, 1),
                _module("Level1", "Level1", p, 2, extends_type="Base"),
                _module("Level2", "Level2", p, 3, extends_type="Level1"),
                _module("Level3", "Level3", p, 4, extends_type="Level2"),
                _module("Level4", "Level4", p, 5, extends_type="Level3"),
            ],
        )

        self.assertEqual(
            [("Level4.check", "Base.ROOT_LIMIT")],
            [(edge.source_id, edge.target_id) for edge in refs],
        )
        symbols = tuple(_build_reference_context({
            "variables": [],
            "constants": [_const("Base.ROOT_LIMIT", "Base", "ROOT_LIMIT", p)],
            "references_edges": refs,
            "init_by_edges": [],
        })["Level4.check"])
        self.assertIn("ROOT_LIMIT", dump_referenced_symbols(symbols))
        context = AnalysisContext(
            id="Level4.check",
            signature="check()",
            file_path=p,
            start_line=5,
            end_line=5,
            referenced_symbols=symbols,
        )
        profile, _ = build_code_semantic_plan(
            _fn("Level4.check", "Level4", p, 5, 5), context,
        )
        self.assertEqual(1, len(profile.symbol_refs))
        self.assertEqual(
            "SYMBOL",
            profile.evidence[profile.symbol_refs[0]].kind,
        )

    async def test_generic_commas_do_not_split_implemented_parent_identity(self):
        src = self.dir / "Generic.java"
        src.write_text(
            "interface Contract { int POLICY = 1; }\n"
            "interface Audited {}\n"
            "class Service { void run() { use(POLICY); } }\n",
            encoding="utf-8",
        )
        p = str(src)
        refs, _ = await link_references(
            code_nodes=[_fn("Service.run", "Service", p, 3, 3)],
            variables=[],
            constants=[_const("Contract.POLICY", "Contract", "POLICY", p)],
            import_refs=[],
            modules=[
                _module("Contract", "Contract", p, 1),
                _module("Audited", "Audited", p, 2),
                _module(
                    "Service", "Service", p, 3,
                    implements_types="Contract<Map<Key, Value>>, Audited",
                ),
            ],
        )

        self.assertEqual(["Contract.POLICY"], [edge.target_id for edge in refs])

    async def test_inheritance_cycle_is_closed_once_without_depth_limit(self):
        src = self.dir / "Cycle.java"
        src.write_text(
            "class A { void run() { use(B_POLICY); } }\n"
            "class B { static final int B_POLICY = 1; }\n",
            encoding="utf-8",
        )
        p = str(src)
        refs, _ = await link_references(
            code_nodes=[_fn("A.run", "A", p, 1, 1)],
            variables=[],
            constants=[_const("B.B_POLICY", "B", "B_POLICY", p)],
            import_refs=[],
            modules=[
                _module("A", "A", p, 1, extends_type="B"),
                _module("B", "B", p, 2, extends_type="A"),
            ],
        )

        self.assertEqual(["B.B_POLICY"], [edge.target_id for edge in refs])

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
            modules=[_module("Base", "Base", p, 1),
                     _module("Order", "Order", p, 4, extends_type="Base")],
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
            modules=[_module("Base", "Base", p, 1),
                     _module("Order", "Order", p, 4, extends_type="Base")],
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
            modules=[_module("Base", "Base", p, 1),
                     _module("Svc", "Svc", p, 3, extends_type="Base")],
        )
        self.assertEqual([r.target_id for r in refs], ["Base.DEFAULT_CFG"])

    async def test_python_multiple_inheritance_uses_exact_c3_mro_not_breadth_first(self):
        src = self.dir / "mro.py"
        src.write_text(
            "class Root: POLICY = 'root'\n"
            "class Left: pass\n"
            "class Right: POLICY = 'right'\n"
            "class Child:\n"
            "    def run(self): use(POLICY)\n",
            encoding="utf-8",
        )
        p = str(src)
        refs, _ = await link_references(
            code_nodes=[_fn("Child.run", "Child", p, 4, 5)],
            variables=[],
            constants=[
                _const("Root.POLICY", "Root", "POLICY", p, "'root'"),
                _const("Right.POLICY", "Right", "POLICY", p, "'right'"),
            ],
            import_refs=[],
            modules=[
                _module("Root", "Root", p, 1),
                _module("Left", "Left", p, 2, extends_type="Root"),
                _module("Right", "Right", p, 3),
                _module("Child", "Child", p, 4, extends_type="Left, Right"),
            ],
        )

        self.assertEqual(["Root.POLICY"], [edge.target_id for edge in refs])

    async def test_inconsistent_python_c3_hierarchy_fails_closed(self):
        src = self.dir / "invalid_mro.py"
        src.write_text("class X: pass\n", encoding="utf-8")
        p = str(src)
        modules = [
            _module("X", "X", p, 1),
            _module("Y", "Y", p, 1),
            _module("A", "A", p, 1, extends_type="X, Y"),
            _module("B", "B", p, 1, extends_type="Y, X"),
            _module("C", "C", p, 1, extends_type="A, B"),
        ]

        with self.assertRaisesRegex(ValueError, "inconsistent Python C3"):
            await link_references(
                code_nodes=[], variables=[], constants=[], import_refs=[], modules=modules,
            )

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
                     _module("C", "C", p, 6, extends_type="Base")],
        )
        self.assertEqual(refs, [])


if __name__ == "__main__":
    unittest.main()
