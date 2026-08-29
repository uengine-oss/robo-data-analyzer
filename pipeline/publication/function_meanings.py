"""Project validated routine meanings into one immutable product patch batch."""
from __future__ import annotations

from typing import Iterable

from graph.product_write_batch import (
    NodeIdentity,
    NodePropertySetOperation,
    ProductRecordExpectation,
    ProductWriteBatch,
)
from graph.schema.graph_nodes import CodeNode
from graph.schema.graph_edges import ReadsWrites
from pipeline.languages import ROUTINE_LABELS
from pipeline.models.function_meaning import (
    FUNCTION_MEANING_PRODUCT_PROPERTY_KEYS,
    function_meaning_product_properties,
)
from pipeline.publication.function_table_usages import (
    FunctionTableUsagesProjectionError,
    function_table_usage_patch_records,
)


class FunctionMeaningsProjectionError(ValueError):
    """A routine's final meaning cannot be published under the closed contract."""


def build_function_meanings_write_batch(
    *,
    analysis_scope_id: str,
    generation_id: str,
    code_nodes: Iterable[CodeNode],
    reads_writes_edges: Iterable[ReadsWrites] = (),
) -> ProductWriteBatch:
    """Build exact property patches for every routine in the analyzed closure."""
    nodes = list(code_nodes)
    ids = [str(node.id or "").strip() for node in nodes]
    if any(not stable_id for stable_id in ids) or len(ids) != len(set(ids)):
        raise FunctionMeaningsProjectionError(
            "CodeNode IDs must be unique and non-blank"
        )

    patches: list[NodePropertySetOperation] = []
    expectations: list[ProductRecordExpectation] = []
    for function in sorted(
        (node for node in nodes if node.label in ROUTINE_LABELS),
        key=lambda node: node.id,
    ):
        try:
            properties = function_meaning_product_properties(function)
        except ValueError as error:
            raise FunctionMeaningsProjectionError(str(error)) from error
        identity = NodeIdentity("CodeNode", function.id)
        patches.extend(
            NodePropertySetOperation.from_value(identity, key, value)
            for key, value in sorted(properties.items())
        )
        expectations.append(ProductRecordExpectation.from_properties(
            identity=identity,
            closed_property_keys=FUNCTION_MEANING_PRODUCT_PROPERTY_KEYS,
            present_properties=properties,
        ))

    try:
        relationship_patches, relationship_expectations = (
            function_table_usage_patch_records(
                code_nodes=nodes,
                reads_writes_edges=reads_writes_edges,
            )
        )
    except FunctionTableUsagesProjectionError as error:
        raise FunctionMeaningsProjectionError(str(error)) from error
    expectations.extend(relationship_expectations)

    return ProductWriteBatch.build(
        stage_name="function_meanings",
        analysis_scope_id=analysis_scope_id,
        generation_id=generation_id,
        node_property_set_operations=patches,
        relationship_property_set_operations=relationship_patches,
        expectations=expectations,
    )
