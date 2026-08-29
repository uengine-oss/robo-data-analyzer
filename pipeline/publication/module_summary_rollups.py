"""Build Module rollups from byte-identical validated function summaries."""
from __future__ import annotations

from collections import defaultdict
from typing import Iterable

from graph.product_write_batch import (
    NodeIdentity,
    NodePropertySetOperation,
    ProductRecordExpectation,
    ProductWriteBatch,
)
from graph.schema.graph_nodes import CodeNode, Module
from pipeline.languages import ROUTINE_LABELS


class ModuleSummaryRollupsProjectionError(ValueError):
    """Function ownership prevents a deterministic Module summary rollup."""


MODULE_SUMMARY_ROLLUP_PROPERTY_KEYS = frozenset({
    "summary_rollup_function_ids",
    "summary_rollup_summaries",
})


def build_module_summary_rollups_write_batch(
    *,
    analysis_scope_id: str,
    generation_id: str,
    modules: Iterable[Module],
    code_nodes: Iterable[CodeNode],
) -> ProductWriteBatch:
    """Patch only Modules whose complete owned routine population has summaries."""
    module_list = list(modules)
    module_ids = [str(module.id or "").strip() for module in module_list]
    if any(not stable_id for stable_id in module_ids) or len(module_ids) != len(
        set(module_ids)
    ):
        raise ModuleSummaryRollupsProjectionError(
            "Module IDs must be unique and non-blank"
        )
    module_id_set = set(module_ids)

    routines = [node for node in code_nodes if node.label in ROUTINE_LABELS]
    routine_ids = [str(function.id or "").strip() for function in routines]
    if any(not stable_id for stable_id in routine_ids) or len(routine_ids) != len(
        set(routine_ids)
    ):
        raise ModuleSummaryRollupsProjectionError(
            "routine IDs must be unique and non-blank"
        )

    routines_by_module: dict[str, list[CodeNode]] = defaultdict(list)
    for function in routines:
        owner_id = str(function.owner_id or "").strip()
        if owner_id not in module_id_set:
            raise ModuleSummaryRollupsProjectionError(
                f"{function.id}: owner_id does not identify a loaded Module: "
                f"{owner_id!r}"
            )
        routines_by_module[owner_id].append(function)
    for functions in routines_by_module.values():
        functions.sort(key=lambda function: function.id)

    patches: list[NodePropertySetOperation] = []
    expectations: list[ProductRecordExpectation] = []
    for module in sorted(module_list, key=lambda item: item.id):
        functions = routines_by_module.get(module.id, ())
        if not functions:
            continue
        summaries = tuple(
            str(function.summary or "")
            for function in functions
        )
        if any(not summary.strip() for summary in summaries):
            # The final product must have neither rollup property when even one
            # owned routine has no publishable summary. The generation closure
            # records this Module as unavailable rather than publishing a prefix.
            continue
        properties = {
            "summary_rollup_function_ids": tuple(
                function.id for function in functions
            ),
            "summary_rollup_summaries": summaries,
        }
        identity = NodeIdentity("Module", module.id)
        patches.extend(
            NodePropertySetOperation.from_value(identity, key, value)
            for key, value in sorted(properties.items())
        )
        expectations.append(ProductRecordExpectation.from_properties(
            identity=identity,
            closed_property_keys=MODULE_SUMMARY_ROLLUP_PROPERTY_KEYS,
            present_properties=properties,
        ))

    return ProductWriteBatch.build(
        stage_name="module_summary_rollups",
        analysis_scope_id=analysis_scope_id,
        generation_id=generation_id,
        node_property_set_operations=patches,
        expectations=expectations,
    )
