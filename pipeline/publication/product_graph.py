"""Project the complete workflow result into the small product graph."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

from graph.product_graph import ProductGraph, ProductNode, ProductRelationship
from graph.schema.graph_edges import (
    Calls,
    Fk,
    HasColumn,
    HasRule,
    InitBy,
    ParentOf,
    ReadsWrites,
    References,
    StatementNext,
)
from graph.schema.graph_nodes import (
    CodeNode,
    Column,
    Constant,
    DataType,
    Module,
    Package,
    Rule,
    Table,
    Variable,
)
from pipeline.source_languages import ROUTINE_LABELS
from pipeline.models.external_reference_resolution import ResolvedExternalReferenceLink
from pipeline.models.external_source import ExternalSourceRecord
from shared.files.source_excerpt import number_text


@dataclass(frozen=True)
class ProductGraphInput:
    packages: Sequence[Package]
    modules: Sequence[Module]
    code_nodes: Sequence[CodeNode]
    variables: Sequence[Variable]
    constants: Sequence[Constant]
    data_types: Sequence[DataType]

    parent_of_edges: Sequence[ParentOf]
    statement_next_edges: Sequence[StatementNext]
    references_edges: Sequence[References]
    init_by_edges: Sequence[InitBy]

    tables: Sequence[Table]
    columns: Sequence[Column]
    has_column_edges: Sequence[HasColumn]
    foreign_key_edges: Sequence[Fk]
    reads_writes_edges: Sequence[ReadsWrites]

    calls_edges: Sequence[Calls]
    rules: Sequence[Rule]
    has_rule_edges: Sequence[HasRule]
    rule_table_edges: Sequence[ReadsWrites]

    external_source_records: Sequence[ExternalSourceRecord]
    resolved_external_reference_links: Sequence[ResolvedExternalReferenceLink]
    routine_embeddings: Sequence[Mapping[str, object]]


def _source_properties(node: object) -> dict[str, object]:
    properties: dict[str, object] = {
        "name": getattr(node, "name", ""),
        "file_path": getattr(node, "file_path", ""),
        "start_line": getattr(node, "start_line", 0),
        "end_line": getattr(node, "end_line", 0),
        "code_text": getattr(node, "code_text", ""),
    }
    if isinstance(node, CodeNode):
        properties.update({
            "signature": node.signature,
            "summary": node.summary,
            "stereotype": node.stereotype,
        })
    elif isinstance(node, Variable):
        properties.update({
            "data_type": node.var_type,
            "value": node.init_value,
        })
    elif isinstance(node, Constant):
        properties.update({"data_type": node.var_type, "value": node.value})
    return properties


def _search_vectors(
    embeddings: Sequence[Mapping[str, object]],
) -> dict[str, list[float]]:
    vectors: dict[str, list[float]] = {}
    for item in embeddings:
        routine_id = str(item.get("routine_id") or "").strip()
        vector = item.get("embedding")
        if not routine_id or not isinstance(vector, (list, tuple)) or not vector:
            raise ValueError("routine search embedding requires a routine ID and vector")
        if routine_id in vectors:
            raise ValueError(f"duplicate routine search embedding: {routine_id!r}")
        vectors[routine_id] = [float(value) for value in vector]
    return vectors


def _rule_code_text(rule: Rule, owner: CodeNode) -> str:
    relative = rule.anchor_line - owner.start_line
    raw_lines = owner.raw_code_text.splitlines()
    if 0 <= relative < len(raw_lines):
        return number_text(raw_lines[relative], rule.anchor_line)
    prefix = f"{rule.anchor_line} |"
    for line in owner.code_text.splitlines():
        if line.lstrip().startswith(prefix):
            return line
    raise ValueError(f"Rule source line is outside its routine: {rule.id}")


def _number_external(record: ExternalSourceRecord) -> str:
    return number_text(record.exact_text, record.source_start_line or 1)


def _resolved_inheritance_target(
    raw_name: str,
    *,
    source: Module,
    modules: Sequence[Module],
) -> str | None:
    name = raw_name.strip()
    if not name:
        return None
    exact = [module.id for module in modules if module.id == name]
    if len(exact) == 1:
        return exact[0]
    local = [
        module.id for module in modules
        if module.file_path == source.file_path and module.name == name
    ]
    return local[0] if len(local) == 1 else None


def _put_relationship(
    target: dict[tuple[str, str, str, str, str], ProductRelationship],
    relationship_type: str,
    source_id: str,
    target_id: str,
    properties: Mapping[str, object] | None = None,
) -> None:
    relationship = ProductRelationship.build(
        relationship_type, source_id, target_id, properties,
    )
    key = (
        relationship.relationship_type,
        source_id,
        target_id,
        str(relationship.properties.get("from_column") or ""),
        str(relationship.properties.get("to_column") or ""),
    )
    previous = target.get(key)
    if previous is not None:
        if relationship_type in {"READS", "WRITES"}:
            operations = sorted({
                *previous.properties.get("operations", []),
                *relationship.properties.get("operations", []),
            })
            relationship = ProductRelationship.build(
                relationship_type,
                source_id,
                target_id,
                {"operations": operations} if operations else None,
            )
        elif dict(previous.properties) != dict(relationship.properties):
            raise ValueError(f"conflicting relationship properties: {key!r}")
    target[key] = relationship


def build_product_graph(product_input: ProductGraphInput) -> ProductGraph:
    """Build and close the entire graph without touching Neo4j."""
    nodes: list[ProductNode] = []
    vectors = _search_vectors(product_input.routine_embeddings)

    for package in product_input.packages:
        nodes.append(ProductNode.build(
            category="package",
            label="PACKAGE",
            node_id=package.id,
            properties={"name": package.name, "qualified_name": package.id},
        ))

    source_nodes = (
        *product_input.modules,
        *product_input.code_nodes,
        *product_input.variables,
        *product_input.constants,
        *product_input.data_types,
    )
    for node in source_nodes:
        properties = _source_properties(node)
        if isinstance(node, CodeNode) and node.label in ROUTINE_LABELS:
            vector = vectors.pop(node.id, None)
            if vector is not None:
                properties["_search_embedding"] = vector
        nodes.append(ProductNode.build(
            category="source",
            label=node.label,
            node_id=node.id,
            properties=properties,
        ))
    if vectors:
        raise ValueError(
            f"search embeddings refer to missing routines: {sorted(vectors)}"
        )

    for table in product_input.tables:
        nodes.append(ProductNode.build(
            category="table",
            label="TABLE",
            node_id=table.id,
            properties={
                "datasource": table.db,
                "database": table.catalog,
                "schema": table.schema,
                "name": table.name,
                "qualified_name": table.id,
                "logical_name": table.logical_name,
                "table_type": table.table_type,
                "description": table.description,
                "summary": table.analyzed_description,
            },
        ))
    for column in product_input.columns:
        nodes.append(ProductNode.build(
            category="column",
            label="COLUMN",
            node_id=column.id,
            properties={
                "name": column.name,
                "logical_name": column.logical_name,
                "data_type": column.dtype,
                "nullable": column.nullable,
                "primary_key": column.is_primary_key,
                "unique": column.is_unique,
                "default_value": column.default_value,
                "description": column.description,
                "summary": column.analyzed_description,
            },
        ))

    code_by_id = {node.id: node for node in product_input.code_nodes}
    owner_by_rule_id = {
        edge.target_id: code_by_id.get(edge.source_id)
        for edge in product_input.has_rule_edges
    }
    for rule in product_input.rules:
        owner = owner_by_rule_id.get(rule.id)
        if owner is None:
            raise ValueError(f"Rule has no routine owner: {rule.id}")
        nodes.append(ProductNode.build(
            category="rule",
            label="RULE",
            node_id=rule.id,
            properties={
                "line": rule.anchor_line,
                "condition": rule.structural_condition,
                "effects": rule.structural_effects,
                "condition_description": rule.condition_description,
                "effect_descriptions": rule.effect_descriptions,
                "code_text": _rule_code_text(rule, owner),
            },
        ))

    for record in product_input.external_source_records:
        nodes.append(ProductNode.build(
            category="external",
            label="EXTERNAL_CHUNK",
            node_id=record.source_record_id,
            properties={
                "key": list(record.addressable_keys),
                "kind": record.semantic_kind,
                "file_path": record.project_relative_path,
                "start_line": record.source_start_line,
                "end_line": record.source_end_line,
                "code_text": _number_external(record),
            },
        ))

    relationships: dict[tuple[str, str, str, str, str], ProductRelationship] = {}
    known_ids = {node.node_id for node in nodes}
    for edge in product_input.parent_of_edges:
        _put_relationship(
            relationships, "PARENT_OF", edge.parent_id, edge.child_id,
        )
    for edge in product_input.statement_next_edges:
        _put_relationship(relationships, "NEXT", edge.prev_id, edge.next_id)
    for edge in product_input.references_edges:
        _put_relationship(
            relationships, "REFERENCES", edge.source_id, edge.target_id,
        )
    for edge in product_input.init_by_edges:
        _put_relationship(
            relationships, "INIT_BY", edge.from_id, edge.source_id,
        )

    for module in product_input.modules:
        target = _resolved_inheritance_target(
            module.extends_type, source=module, modules=product_input.modules,
        )
        if target:
            _put_relationship(relationships, "EXTENDS", module.id, target)
        for raw_name in module.implements_types.split(","):
            target = _resolved_inheritance_target(
                raw_name, source=module, modules=product_input.modules,
            )
            if target:
                _put_relationship(relationships, "IMPLEMENTS", module.id, target)

    for edge in product_input.has_column_edges:
        _put_relationship(
            relationships, "HAS_COLUMN", edge.parent_id, edge.child_id,
        )
    for edge in product_input.foreign_key_edges:
        _put_relationship(
            relationships,
            "FK",
            edge.from_id,
            edge.to_id,
            {"from_column": edge.from_column, "to_column": edge.to_column},
        )
    for edge in product_input.calls_edges:
        _put_relationship(
            relationships, "CALLS", edge.caller_id, edge.callee_id,
        )
    for edge in product_input.reads_writes_edges:
        operations = edge.operations or (
            (edge.dml_type.strip().upper(),) if edge.dml_type.strip() else ()
        )
        _put_relationship(
            relationships,
            edge.relation,
            edge.source_id,
            edge.target_id,
            {"operations": list(operations)} if operations else None,
        )
    for edge in product_input.has_rule_edges:
        _put_relationship(
            relationships,
            "HAS_RULE",
            edge.source_id,
            edge.target_id,
            {"order": edge.ordinal},
        )
    for edge in product_input.rule_table_edges:
        _put_relationship(
            relationships,
            edge.relation,
            edge.source_id,
            edge.target_id,
            {"operations": list(edge.operations)} if edge.operations else None,
        )
    for link in product_input.resolved_external_reference_links:
        _put_relationship(
            relationships,
            "REFERENCES",
            link.owner_code_node_id,
            link.external_source_record_id,
        )

    outside = [
        key for key, relationship in relationships.items()
        if relationship.source_id not in known_ids or relationship.target_id not in known_ids
    ]
    if outside:
        raise ValueError(f"product relationships have missing endpoints: {outside[:5]}")
    return ProductGraph.build(nodes=nodes, relationships=relationships.values())
