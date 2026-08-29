"""DB-independent publication of deterministic code components."""
from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence

from graph.product_write_batch import (
    NodeCreateOperation,
    NodeIdentity,
    ProductRecordExpectation,
    ProductWriteBatch,
    RelationshipCreateOperation,
    RelationshipIdentity,
)
from graph.schema.graph_nodes import CodeNode
from pipeline.languages import ROUTINE_LABELS


COMPONENT_PROPERTY_KEYS = frozenset({
    "stable_id",
    "analysis_scope_id",
    "generation_id",
})
MEMBER_OF_COMPONENT_PROPERTY_KEYS = frozenset({
    "analysis_scope_id",
    "generation_id",
})


class ComponentsProjectionError(ValueError):
    """Deterministic component membership is not a closed product population."""


def build_components_write_batch(
    *,
    analysis_scope_id: str,
    generation_id: str,
    code_nodes: Iterable[CodeNode],
    member_ids_by_component_id: Mapping[str, Sequence[str]],
) -> ProductWriteBatch:
    """Create Component nodes and exact routine membership relationships.

    Component identity and membership are sufficient product facts.  A display
    name and member count are deliberately absent: no source-owned component
    name exists, and the count is recoverable from MEMBER_OF_COMPONENT.
    """
    nodes = list(code_nodes)
    code_ids = [str(node.id or "").strip() for node in nodes]
    if any(not stable_id for stable_id in code_ids) or len(code_ids) != len(
        set(code_ids)
    ):
        raise ComponentsProjectionError("CodeNode IDs must be unique and non-blank")
    routine_ids = {
        node.id for node in nodes if node.label in ROUTINE_LABELS
    }

    node_operations: list[NodeCreateOperation] = []
    relationship_operations: list[RelationshipCreateOperation] = []
    expectations: list[ProductRecordExpectation] = []
    assigned_members: set[str] = set()

    for component_id, raw_member_ids in sorted(member_ids_by_component_id.items()):
        stable_id = str(component_id or "").strip()
        if not stable_id:
            raise ComponentsProjectionError("Component ID must be non-blank")
        member_ids = [str(member_id or "").strip() for member_id in raw_member_ids]
        if any(not member_id for member_id in member_ids):
            raise ComponentsProjectionError(
                f"Component contains a blank member ID: {stable_id!r}"
            )
        if member_ids != sorted(member_ids) or len(member_ids) != len(set(member_ids)):
            raise ComponentsProjectionError(
                f"Component members must be unique and sorted: {stable_id!r}"
            )
        if len(member_ids) < 2:
            raise ComponentsProjectionError(
                f"Component must contain at least two routines: {stable_id!r}"
            )
        expected_id = f"COMPONENT:{member_ids[0]}"
        if stable_id != expected_id:
            raise ComponentsProjectionError(
                f"Component ID differs from its deterministic member identity: "
                f"expected={expected_id!r}, actual={stable_id!r}"
            )
        unknown = sorted(set(member_ids) - routine_ids)
        if unknown:
            raise ComponentsProjectionError(
                f"Component member is not a verified routine: {unknown[0]!r}"
            )
        overlap = assigned_members & set(member_ids)
        if overlap:
            raise ComponentsProjectionError(
                f"routine belongs to multiple Components: {sorted(overlap)[0]!r}"
            )
        assigned_members.update(member_ids)

        component_identity = NodeIdentity("Component", stable_id)
        component_properties = {
            "stable_id": stable_id,
            "analysis_scope_id": analysis_scope_id,
            "generation_id": generation_id,
        }
        node_operations.append(NodeCreateOperation.from_properties(
            component_identity,
            component_properties,
        ))
        expectations.append(ProductRecordExpectation.from_properties(
            identity=component_identity,
            closed_property_keys=COMPONENT_PROPERTY_KEYS,
            present_properties=component_properties,
        ))

        for member_id in member_ids:
            relationship_identity = RelationshipIdentity(
                "MEMBER_OF_COMPONENT",
                NodeIdentity("CodeNode", member_id),
                component_identity,
            )
            relationship_properties = {
                "analysis_scope_id": analysis_scope_id,
                "generation_id": generation_id,
            }
            relationship_operations.append(
                RelationshipCreateOperation.from_properties(
                    relationship_identity,
                    relationship_properties,
                )
            )
            expectations.append(ProductRecordExpectation.from_properties(
                identity=relationship_identity,
                closed_property_keys=MEMBER_OF_COMPONENT_PROPERTY_KEYS,
                present_properties=relationship_properties,
            ))

    return ProductWriteBatch.build(
        stage_name="components",
        analysis_scope_id=analysis_scope_id,
        generation_id=generation_id,
        node_create_operations=node_operations,
        relationship_create_operations=relationship_operations,
        expectations=expectations,
    )
