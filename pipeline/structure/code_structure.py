"""Project verified Parser structure into one immutable product write batch.

The Parser has already established source identity, exact source bytes, syntax
kinds, ranges, containment, and source order.  This module does not read source
text and does not infer syntax.  It only validates the Analyzer's current
structure models and translates their product-visible subset into the closed
Package -> File -> Module -> CodeNode hierarchy.
"""
from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Iterable, Mapping

from graph.product_write_batch import (
    NodeCreateOperation,
    NodeIdentity,
    ProductRecordExpectation,
    ProductWriteBatch,
    RelationshipCreateOperation,
    RelationshipIdentity,
)
from graph.schema.graph_edges import BelongsTo, HasMember, ParentOf, StatementNext
from graph.schema.graph_nodes import CodeNode, Module, Package
from pipeline.steps.step2_load_ast.source_ast_closure import SourceAstClosure


class CodeStructureProjectionError(ValueError):
    """Verified Parser structure cannot be published without loss or guessing."""


def _unique_by_id(items: Iterable[object], *, kind: str) -> dict[str, object]:
    indexed: dict[str, object] = {}
    for item in items:
        stable_id = str(getattr(item, "id", "") or "").strip()
        if not stable_id:
            raise CodeStructureProjectionError(f"{kind} has an empty id")
        if stable_id in indexed:
            raise CodeStructureProjectionError(
                f"duplicate {kind} id: {stable_id!r}"
            )
        indexed[stable_id] = item
    return indexed


def _source_artifacts_by_path(closure: SourceAstClosure) -> dict[Path, object]:
    artifacts: dict[Path, object] = {}
    source_ids: set[str] = set()
    for artifact in closure.artifacts:
        source_path = artifact.source_path.resolve()
        if source_path in artifacts:
            raise CodeStructureProjectionError(
                f"duplicate source path in Parser closure: {source_path}"
            )
        if artifact.source_id in source_ids:
            raise CodeStructureProjectionError(
                f"duplicate source id in Parser closure: {artifact.source_id!r}"
            )
        artifacts[source_path] = artifact
        source_ids.add(artifact.source_id)
    if not artifacts:
        raise CodeStructureProjectionError("Parser source/AST closure is empty")
    return artifacts


def _artifact_for_file_path(
    file_path: str,
    artifacts_by_path: Mapping[Path, object],
    *,
    owner_kind: str,
    owner_id: str,
) -> object:
    if not str(file_path or "").strip():
        raise CodeStructureProjectionError(
            f"{owner_kind} {owner_id!r} has an empty file path"
        )
    resolved = Path(file_path).resolve()
    artifact = artifacts_by_path.get(resolved)
    if artifact is None:
        raise CodeStructureProjectionError(
            f"{owner_kind} {owner_id!r} points outside the sealed Parser sources: "
            f"{file_path}"
        )
    return artifact


def _node_record(
    identity: NodeIdentity,
    properties: Mapping[str, object],
) -> tuple[NodeCreateOperation, ProductRecordExpectation]:
    operation = NodeCreateOperation.from_properties(identity, properties)
    expectation = ProductRecordExpectation.from_properties(
        identity=identity,
        closed_property_keys=properties,
        present_properties=properties,
    )
    return operation, expectation


def _relationship_record(
    identity: RelationshipIdentity,
    properties: Mapping[str, object],
) -> tuple[RelationshipCreateOperation, ProductRecordExpectation]:
    operation = RelationshipCreateOperation.from_properties(identity, properties)
    expectation = ProductRecordExpectation.from_properties(
        identity=identity,
        closed_property_keys=properties,
        present_properties=properties,
    )
    return operation, expectation


def _assert_no_code_parent_cycle(parent_by_child: Mapping[str, str]) -> None:
    for first_child in parent_by_child:
        visited: set[str] = set()
        current = first_child
        while current in parent_by_child:
            if current in visited:
                raise CodeStructureProjectionError(
                    f"CodeNode containment cycle includes {current!r}"
                )
            visited.add(current)
            current = parent_by_child[current]


def build_code_structure_write_batch(
    *,
    analysis_scope_id: str,
    generation_id: str,
    source_ast_closure: SourceAstClosure,
    packages: Iterable[Package],
    modules: Iterable[Module],
    code_nodes: Iterable[CodeNode],
    belongs_to_edges: Iterable[BelongsTo],
    has_member_edges: Iterable[HasMember],
    parent_of_edges: Iterable[ParentOf],
    statement_next_edges: Iterable[StatementNext],
) -> ProductWriteBatch:
    """Build the complete ``code_structure`` publication obligation.

    Every sealed source must own at least one Module, every Module must map to
    exactly one sealed source, and every CodeNode must have exactly one product
    parent: its enclosing CodeNode or its owner Module.  Any mismatch fails
    before Neo4j is opened.
    """
    package_by_id = _unique_by_id(packages, kind="Package")
    module_by_id = _unique_by_id(modules, kind="Module")
    code_by_id = _unique_by_id(code_nodes, kind="CodeNode")
    artifacts_by_path = _source_artifacts_by_path(source_ast_closure)

    package_identity = {
        stable_id: NodeIdentity("Package", stable_id)
        for stable_id in package_by_id
    }
    file_identity = {
        artifact.source_id: NodeIdentity("File", artifact.source_id)
        for artifact in artifacts_by_path.values()
    }
    module_identity = {
        stable_id: NodeIdentity("Module", stable_id)
        for stable_id in module_by_id
    }
    code_identity = {
        stable_id: NodeIdentity("CodeNode", stable_id)
        for stable_id in code_by_id
    }

    module_source_id: dict[str, str] = {}
    modules_by_source_id: dict[str, list[Module]] = defaultdict(list)
    for stable_id, raw_module in module_by_id.items():
        module = raw_module
        artifact = _artifact_for_file_path(
            module.file_path,
            artifacts_by_path,
            owner_kind="Module",
            owner_id=stable_id,
        )
        module_source_id[stable_id] = artifact.source_id
        modules_by_source_id[artifact.source_id].append(module)

    missing_module_sources = sorted(set(file_identity) - set(modules_by_source_id))
    if missing_module_sources:
        raise CodeStructureProjectionError(
            "sealed source has no Module: " + missing_module_sources[0]
        )

    code_source_id: dict[str, str] = {}
    for stable_id, raw_node in code_by_id.items():
        node = raw_node
        artifact = _artifact_for_file_path(
            node.file_path,
            artifacts_by_path,
            owner_kind="CodeNode",
            owner_id=stable_id,
        )
        code_source_id[stable_id] = artifact.source_id

    package_for_module: dict[str, str] = {}
    for edge in belongs_to_edges:
        if edge.child_id not in module_by_id:
            continue
        if edge.parent_id not in package_by_id:
            raise CodeStructureProjectionError(
                f"Module {edge.child_id!r} belongs to unknown Package "
                f"{edge.parent_id!r}"
            )
        previous = package_for_module.setdefault(edge.child_id, edge.parent_id)
        if previous != edge.parent_id:
            raise CodeStructureProjectionError(
                f"Module {edge.child_id!r} belongs to multiple Packages"
            )

    package_for_source: dict[str, str] = {}
    for module_id, package_id in package_for_module.items():
        source_id = module_source_id[module_id]
        previous = package_for_source.setdefault(source_id, package_id)
        if previous != package_id:
            raise CodeStructureProjectionError(
                f"source {source_id!r} maps to multiple Packages"
            )
    unused_packages = sorted(set(package_by_id) - set(package_for_source.values()))
    if unused_packages:
        raise CodeStructureProjectionError(
            f"Package has no sealed source child: {unused_packages[0]!r}"
        )

    explicit_member_parent: dict[str, str] = {}
    for edge in has_member_edges:
        if edge.child_id not in code_by_id:
            continue
        if edge.parent_id not in module_by_id:
            raise CodeStructureProjectionError(
                f"CodeNode member {edge.child_id!r} has unknown Module parent "
                f"{edge.parent_id!r}"
            )
        previous = explicit_member_parent.setdefault(edge.child_id, edge.parent_id)
        if previous != edge.parent_id:
            raise CodeStructureProjectionError(
                f"CodeNode {edge.child_id!r} has multiple Module parents"
            )

    parent_by_child: dict[str, str] = {}
    parent_pairs: set[tuple[str, str]] = set()
    for edge in parent_of_edges:
        pair = edge.parent_id, edge.child_id
        if pair in parent_pairs:
            raise CodeStructureProjectionError(
                f"duplicate CodeNode PARENT_OF edge: {pair!r}"
            )
        parent_pairs.add(pair)
        if edge.parent_id not in code_by_id or edge.child_id not in code_by_id:
            raise CodeStructureProjectionError(
                f"CodeNode PARENT_OF endpoint is missing: {pair!r}"
            )
        previous = parent_by_child.setdefault(edge.child_id, edge.parent_id)
        if previous != edge.parent_id:
            raise CodeStructureProjectionError(
                f"CodeNode {edge.child_id!r} has multiple structural parents"
            )
    _assert_no_code_parent_cycle(parent_by_child)

    module_parent_for_top_level: dict[str, str] = {}
    for code_id, raw_node in code_by_id.items():
        node = raw_node
        declared_parent = str(node.parent_id or "").strip()
        actual_parent = parent_by_child.get(code_id, "")
        if declared_parent != actual_parent:
            raise CodeStructureProjectionError(
                f"CodeNode {code_id!r} parent_id disagrees with PARENT_OF: "
                f"node={declared_parent!r}, edge={actual_parent!r}"
            )
        if actual_parent:
            if code_source_id[actual_parent] != code_source_id[code_id]:
                raise CodeStructureProjectionError(
                    f"nested CodeNodes cross sealed source files: "
                    f"{actual_parent!r} -> {code_id!r}"
                )
            continue

        owner_id = str(node.owner_id or "").strip()
        if owner_id not in module_by_id:
            raise CodeStructureProjectionError(
                f"top-level CodeNode {code_id!r} has no valid Module owner"
            )
        if module_source_id[owner_id] != code_source_id[code_id]:
            raise CodeStructureProjectionError(
                f"CodeNode {code_id!r} and owner Module {owner_id!r} "
                "belong to different sealed sources"
            )
        explicit_owner = explicit_member_parent.get(code_id)
        if explicit_owner is not None and explicit_owner != owner_id:
            raise CodeStructureProjectionError(
                f"CodeNode {code_id!r} owner_id disagrees with HAS_MEMBER"
            )
        module_parent_for_top_level[code_id] = owner_id

    orphan_member_edges = sorted(
        code_id for code_id in explicit_member_parent
        if code_id in parent_by_child
    )
    if orphan_member_edges:
        raise CodeStructureProjectionError(
            f"nested CodeNode also has a Module HAS_MEMBER parent: "
            f"{orphan_member_edges[0]!r}"
        )

    next_pairs: set[tuple[str, str]] = set()
    next_by_previous: dict[str, str] = {}
    previous_by_next: dict[str, str] = {}
    for edge in statement_next_edges:
        pair = edge.prev_id, edge.next_id
        if pair in next_pairs:
            raise CodeStructureProjectionError(f"duplicate NEXT edge: {pair!r}")
        next_pairs.add(pair)
        if edge.prev_id not in code_by_id or edge.next_id not in code_by_id:
            raise CodeStructureProjectionError(
                f"NEXT endpoint is missing: {pair!r}"
            )
        if edge.prev_id == edge.next_id:
            raise CodeStructureProjectionError(f"NEXT self-loop: {edge.prev_id!r}")
        if code_source_id[edge.prev_id] != code_source_id[edge.next_id]:
            raise CodeStructureProjectionError(
                f"NEXT crosses sealed source files: {pair!r}"
            )
        previous_target = next_by_previous.setdefault(edge.prev_id, edge.next_id)
        next_source = previous_by_next.setdefault(edge.next_id, edge.prev_id)
        if previous_target != edge.next_id or next_source != edge.prev_id:
            raise CodeStructureProjectionError(
                f"NEXT is not an exact source-order chain: {pair!r}"
            )

    control = {
        "analysis_scope_id": analysis_scope_id,
        "generation_id": generation_id,
    }
    node_operations: list[NodeCreateOperation] = []
    relationship_operations: list[RelationshipCreateOperation] = []
    expectations: list[ProductRecordExpectation] = []

    def add_node(identity: NodeIdentity, properties: Mapping[str, object]) -> None:
        operation, expectation = _node_record(identity, properties)
        node_operations.append(operation)
        expectations.append(expectation)

    def add_relationship(
        identity: RelationshipIdentity,
        properties: Mapping[str, object],
    ) -> None:
        operation, expectation = _relationship_record(identity, properties)
        relationship_operations.append(operation)
        expectations.append(expectation)

    for stable_id, raw_package in sorted(package_by_id.items()):
        package = raw_package
        add_node(package_identity[stable_id], {
            **control,
            "stable_id": stable_id,
            "name": package.name,
            "qualified_name": stable_id,
        })

    for artifact in sorted(
        artifacts_by_path.values(), key=lambda item: item.source_id,
    ):
        add_node(file_identity[artifact.source_id], {
            **control,
            "stable_id": artifact.source_id,
            "relative_path": artifact.source_id,
            "raw_source_sha256": artifact.source_sha256,
            "language": artifact.language,
        })

    for stable_id, raw_module in sorted(module_by_id.items()):
        module = raw_module
        add_node(module_identity[stable_id], {
            **control,
            "stable_id": stable_id,
            "module_kind": module.label,
            "name": module.name,
            "qualified_name": stable_id,
            "start_line": module.start_line,
            "end_line": module.end_line,
        })

    for stable_id, raw_node in sorted(code_by_id.items()):
        node = raw_node
        add_node(code_identity[stable_id], {
            **control,
            "stable_id": stable_id,
            "code_kind": node.label,
            "name": node.name,
            "qualified_name": stable_id,
            "start_line": node.start_line,
            "end_line": node.end_line,
        })

    hierarchy_children: dict[NodeIdentity, list[tuple[tuple[object, ...], NodeIdentity]]] = defaultdict(list)
    for source_id, package_id in package_for_source.items():
        hierarchy_children[package_identity[package_id]].append((
            (source_id,), file_identity[source_id],
        ))
    for source_id, source_modules in modules_by_source_id.items():
        for module in source_modules:
            hierarchy_children[file_identity[source_id]].append((
                (module.start_line, module.end_line, module.id),
                module_identity[module.id],
            ))
    for code_id, module_id in module_parent_for_top_level.items():
        node = code_by_id[code_id]
        hierarchy_children[module_identity[module_id]].append((
            (node.start_line, node.end_line, code_id), code_identity[code_id],
        ))
    for child_id, parent_id in parent_by_child.items():
        node = code_by_id[child_id]
        hierarchy_children[code_identity[parent_id]].append((
            (node.start_line, node.end_line, child_id), code_identity[child_id],
        ))

    hierarchy_pairs: set[tuple[NodeIdentity, NodeIdentity]] = set()
    for parent, children in sorted(
        hierarchy_children.items(), key=lambda item: item[0].canonical_key(),
    ):
        for child_order, (_sort_key, child) in enumerate(sorted(children)):
            pair = parent, child
            if pair in hierarchy_pairs:
                raise CodeStructureProjectionError(
                    f"duplicate product PARENT_OF edge: {parent} -> {child}"
                )
            hierarchy_pairs.add(pair)
            add_relationship(
                RelationshipIdentity("PARENT_OF", parent, child),
                {**control, "child_order": child_order},
            )

    for previous_id, next_id in sorted(next_pairs):
        add_relationship(
            RelationshipIdentity(
                "NEXT", code_identity[previous_id], code_identity[next_id],
            ),
            control,
        )

    return ProductWriteBatch.build(
        stage_name="code_structure",
        analysis_scope_id=analysis_scope_id,
        generation_id=generation_id,
        node_create_operations=sorted(
            node_operations, key=lambda item: item.identity.canonical_key(),
        ),
        relationship_create_operations=sorted(
            relationship_operations,
            key=lambda item: item.identity.canonical_key(),
        ),
        expectations=sorted(
            expectations, key=lambda item: item.identity.canonical_key(),
        ),
    )
