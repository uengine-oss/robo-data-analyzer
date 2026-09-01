from __future__ import annotations

import unittest

from graph.product_graph import ProductGraph, ProductGraphError, ProductNode, ProductRelationship
from graph.schema.graph_edges import HasRule, ParentOf
from graph.schema.graph_nodes import CodeNode, Module, Package, Rule, Variable
from pipeline.publication.product_graph import ProductGraphInput, build_product_graph


def _input(**overrides) -> ProductGraphInput:
    package = Package(id="src", name="src")
    module = Module(
        id="src.Service",
        name="Service",
        file_path="src/Service.java",
        label="CLASS",
        start_line=1,
        end_line=8,
        code_text="1 | class Service {\n2 |   void run() {}\n8 | }",
        raw_code_text="class Service {\n  void run() {}\n\n\n\n\n\n}",
    )
    routine = CodeNode(
        id="src.Service.run",
        signature="run()",
        file_path="src/Service.java",
        start_line=2,
        end_line=2,
        label="METHOD",
        name="run",
        code_text="2 |   void run() {}",
        raw_code_text="  void run() {}",
        owner_id=module.id,
        summary="업무를 실행한다",
        stereotype="Service",
    )
    variable = Variable(
        id="src.Service.value",
        owner_id=module.id,
        name="value",
        file_path="src/Service.java",
        start_line=3,
        end_line=3,
        label="FIELD",
        var_type="int",
        init_value="1",
        code_text="3 |   int value = 1;",
        raw_code_text="  int value = 1;",
    )
    rule = Rule(
        id=f"{routine.id}::R-2",
        anchor_line=2,
        structural_condition='F("ready")',
        structural_effects=('return_value("ok")',),
        condition_description="처리할 준비가 되었을 때",
        effect_descriptions=("성공을 반환한다",),
    )
    values = dict(
        packages=(package,),
        modules=(module,),
        code_nodes=(routine,),
        variables=(variable,),
        constants=(),
        data_types=(),
        parent_of_edges=(
            ParentOf(package.id, module.id),
            ParentOf(module.id, routine.id),
            ParentOf(module.id, variable.id),
        ),
        statement_next_edges=(),
        references_edges=(),
        init_by_edges=(),
        tables=(),
        columns=(),
        has_column_edges=(),
        foreign_key_edges=(),
        reads_writes_edges=(),
        calls_edges=(),
        rules=(rule,),
        has_rule_edges=(HasRule(routine.id, rule.id, 1),),
        rule_table_edges=(),
        external_source_records=(),
        resolved_external_reference_links=(),
        routine_embeddings=({"routine_id": routine.id, "embedding": [0.1, 0.2]},),
    )
    values.update(overrides)
    return ProductGraphInput(**values)


class ProductGraphProjectionTest(unittest.TestCase):
    def test_composite_fk_columns_are_distinct_relationships(self) -> None:
        nodes = tuple(
            ProductNode.build(
                category="table", label="TABLE", node_id=node_id,
                properties={"name": node_id},
            )
            for node_id in ("orders", "customers")
        )
        graph = ProductGraph.build(
            nodes=nodes,
            relationships=(
                ProductRelationship.build(
                    "FK", "orders", "customers",
                    {"from_column": "tenant_id", "to_column": "tenant_id"},
                ),
                ProductRelationship.build(
                    "FK", "orders", "customers",
                    {"from_column": "customer_id", "to_column": "id"},
                ),
            ),
        )
        self.assertEqual(2, len(graph.relationships))

    def test_concrete_nodes_direct_embedding_and_minimal_relationships(self) -> None:
        graph = build_product_graph(_input())
        by_id = {node.node_id: node for node in graph.nodes}

        self.assertEqual("CLASS", by_id["src.Service"].label)
        self.assertEqual("METHOD", by_id["src.Service.run"].label)
        self.assertEqual("FIELD", by_id["src.Service.value"].label)
        self.assertEqual(
            [0.1, 0.2],
            by_id["src.Service.run"].properties["_search_embedding"],
        )
        self.assertEqual("1", by_id["src.Service.value"].properties["value"])
        self.assertEqual(
            {"PACKAGE", "CLASS", "METHOD", "FIELD", "RULE"},
            {node.label for node in graph.nodes},
        )
        self.assertEqual(
            {"PARENT_OF", "HAS_RULE"},
            {edge.relationship_type for edge in graph.relationships},
        )

    def test_source_backed_node_without_code_text_fails_before_db(self) -> None:
        broken = Module(
            id="broken",
            name="broken",
            file_path="broken.py",
            label="FILE",
            start_line=1,
            end_line=1,
        )
        with self.assertRaisesRegex(ProductGraphError, "missing code_text"):
            build_product_graph(_input(
                modules=(broken,),
                parent_of_edges=(),
            ))


if __name__ == "__main__":
    unittest.main()
