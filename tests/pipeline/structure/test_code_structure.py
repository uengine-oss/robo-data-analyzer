from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from graph.schema.graph_edges import BelongsTo, HasMember, ParentOf, StatementNext
from graph.schema.graph_nodes import CodeNode, Module, Package
from pipeline.steps.step2_load_ast.source_ast_closure import (
    SourceAstArtifact,
    SourceAstClosure,
)
from pipeline.structure.code_structure import (
    CodeStructureProjectionError,
    build_code_structure_write_batch,
)


class CodeStructureWriteBatchTest(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.source_a = self.root / "source" / "pkg" / "a.c"
        self.source_b = self.root / "source" / "pkg" / "b.c"
        self.closure = SourceAstClosure(
            source_root=self.root / "source",
            ast_root=self.root / "analysis",
            artifacts=(
                self._artifact("pkg/a.c", self.source_a, "a"),
                self._artifact("pkg/b.c", self.source_b, "b"),
            ),
        )
        self.packages = [Package(id="pkg", name="pkg")]
        self.modules = [
            Module(
                id="module:a", name="a", file_path=str(self.source_a),
                label="FILE", start_line=0, end_line=0,
            ),
            Module(
                id="module:b", name="b", file_path=str(self.source_b),
                label="FILE", start_line=0, end_line=0,
            ),
        ]
        self.root_a = CodeNode(
            id="code:a:run", signature="run()", file_path=str(self.source_a),
            start_line=10, end_line=30, label="FUNCTION", name="run",
            owner_id="module:a",
        )
        self.if_a = CodeNode(
            id="code:a:run:if:12", signature="", file_path=str(self.source_a),
            start_line=12, end_line=18, label="IF", name="if",
            owner_id="module:a", parent_id=self.root_a.id,
        )
        self.call_a = CodeNode(
            id="code:a:run:call:20", signature="", file_path=str(self.source_a),
            start_line=20, end_line=20, label="CALL", name="work",
            owner_id="module:a", parent_id=self.root_a.id,
        )
        self.root_b = CodeNode(
            id="code:b:work", signature="work()", file_path=str(self.source_b),
            start_line=3, end_line=9, label="FUNCTION", name="work",
            owner_id="module:b",
        )
        self.code_nodes = [self.root_a, self.if_a, self.call_a, self.root_b]
        self.belongs_to = [
            BelongsTo(parent_id="pkg", child_id="module:a"),
            BelongsTo(parent_id="pkg", child_id="module:b"),
        ]
        self.has_member = [
            HasMember(parent_id="module:a", child_id=self.root_a.id),
            HasMember(parent_id="module:b", child_id=self.root_b.id),
            # Variable/Constant/DataType members remain Analyzer-internal context.
            HasMember(parent_id="module:a", child_id="variable:local"),
        ]
        self.parent_of = [
            ParentOf(parent_id=self.root_a.id, child_id=self.if_a.id),
            ParentOf(parent_id=self.root_a.id, child_id=self.call_a.id),
        ]
        self.statement_next = [
            StatementNext(prev_id=self.if_a.id, next_id=self.call_a.id),
        ]

    def _artifact(
        self,
        source_id: str,
        source_path: Path,
        marker: str,
    ) -> SourceAstArtifact:
        return SourceAstArtifact(
            source_id=source_id,
            source_path=source_path,
            ast_path=self.root / "analysis" / f"{marker}.json",
            diagnostic_path=self.root / "diagnostics" / f"{marker}.parse.json",
            source_sha256=marker * 64,
            ast_sha256=(marker.upper()) * 64,
            diagnostic_sha256=("d" if marker == "a" else "e") * 64,
            language="c",
            ast_document={"source": source_id, "nodes": []},
        )

    def _build(self, **overrides):
        values = {
            "analysis_scope_id": "scope-1",
            "generation_id": "generation-1",
            "source_ast_closure": self.closure,
            "packages": self.packages,
            "modules": self.modules,
            "code_nodes": self.code_nodes,
            "belongs_to_edges": self.belongs_to,
            "has_member_edges": self.has_member,
            "parent_of_edges": self.parent_of,
            "statement_next_edges": self.statement_next,
        }
        values.update(overrides)
        return build_code_structure_write_batch(**values)

    def test_builds_one_closed_batch_without_internal_context_nodes(self) -> None:
        batch = self._build()

        self.assertEqual(batch.stage_name, "code_structure")
        identities = {
            operation.identity.canonical_key()
            for operation in batch.node_create_operations
        }
        self.assertEqual(
            identities,
            {
                "node\x1fPackage\x1fpkg",
                "node\x1fFile\x1fpkg/a.c",
                "node\x1fFile\x1fpkg/b.c",
                "node\x1fModule\x1fmodule:a",
                "node\x1fModule\x1fmodule:b",
                "node\x1fCodeNode\x1fcode:a:run",
                "node\x1fCodeNode\x1fcode:a:run:if:12",
                "node\x1fCodeNode\x1fcode:a:run:call:20",
                "node\x1fCodeNode\x1fcode:b:work",
            },
        )
        self.assertFalse(any("variable:local" in key for key in identities))

        file_a = next(
            operation for operation in batch.node_create_operations
            if operation.identity.product_type == "File"
            and operation.identity.stable_id == "pkg/a.c"
        )
        self.assertEqual(
            dict(file_a.present_properties),
            {
                "analysis_scope_id": "scope-1",
                "generation_id": "generation-1",
                "language": "c",
                "raw_source_sha256": "a" * 64,
                "relative_path": "pkg/a.c",
                "stable_id": "pkg/a.c",
            },
        )

    def test_hierarchy_and_next_are_exact_and_source_ordered(self) -> None:
        batch = self._build()
        relationships = {
            (
                operation.identity.relationship_type,
                operation.identity.source.product_type,
                operation.identity.source.stable_id,
                operation.identity.target.product_type,
                operation.identity.target.stable_id,
            ): dict(operation.present_properties)
            for operation in batch.relationship_create_operations
        }

        self.assertEqual(relationships[
            ("PARENT_OF", "Package", "pkg", "File", "pkg/a.c")
        ]["child_order"], 0)
        self.assertEqual(relationships[
            ("PARENT_OF", "Package", "pkg", "File", "pkg/b.c")
        ]["child_order"], 1)
        self.assertIn(
            ("PARENT_OF", "File", "pkg/a.c", "Module", "module:a"),
            relationships,
        )
        self.assertIn(
            ("PARENT_OF", "Module", "module:a", "CodeNode", "code:a:run"),
            relationships,
        )
        self.assertEqual(relationships[
            ("PARENT_OF", "CodeNode", "code:a:run", "CodeNode", "code:a:run:if:12")
        ]["child_order"], 0)
        self.assertEqual(relationships[
            ("PARENT_OF", "CodeNode", "code:a:run", "CodeNode", "code:a:run:call:20")
        ]["child_order"], 1)
        self.assertIn(
            ("NEXT", "CodeNode", "code:a:run:if:12", "CodeNode", "code:a:run:call:20"),
            relationships,
        )

    def test_input_order_does_not_change_the_batch(self) -> None:
        forward = self._build()
        reversed_batch = self._build(
            packages=reversed(self.packages),
            modules=reversed(self.modules),
            code_nodes=reversed(self.code_nodes),
            belongs_to_edges=reversed(self.belongs_to),
            has_member_edges=reversed(self.has_member),
            parent_of_edges=reversed(self.parent_of),
            statement_next_edges=reversed(self.statement_next),
        )

        self.assertEqual(
            forward.canonical_batch_sha256,
            reversed_batch.canonical_batch_sha256,
        )
        self.assertEqual(forward.artifact_dict(), reversed_batch.artifact_dict())

    def test_sealed_source_without_module_fails_before_write(self) -> None:
        with self.assertRaisesRegex(
            CodeStructureProjectionError,
            "sealed source has no Module: pkg/b.c",
        ):
            self._build(
                modules=self.modules[:1],
                code_nodes=self.code_nodes[:3],
                belongs_to_edges=self.belongs_to[:1],
                has_member_edges=self.has_member[:2],
            )

    def test_parent_model_and_edge_disagreement_fails_before_write(self) -> None:
        with self.assertRaisesRegex(
            CodeStructureProjectionError,
            "parent_id disagrees with PARENT_OF",
        ):
            self._build(parent_of_edges=self.parent_of[:1])

    def test_cross_source_next_fails_before_write(self) -> None:
        with self.assertRaisesRegex(
            CodeStructureProjectionError,
            "NEXT crosses sealed source files",
        ):
            self._build(statement_next_edges=[
                StatementNext(prev_id=self.call_a.id, next_id=self.root_b.id),
            ])


if __name__ == "__main__":
    unittest.main()
