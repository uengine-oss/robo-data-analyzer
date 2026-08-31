"""Assemble every Analyzer product stage into one closed publication plan.

This is the only DB-independent point where the complete final product is
defined.  Stage builders retain their narrow ownership, while this module
proves that none of the required stages was omitted before any product write.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

from graph.product_write_batch import (
    ProductWriteContractError,
    ProductWritePlan,
    combine_product_write_batches,
)
from graph.publication_schema import PRODUCT_STAGE_NAMES
from graph.schema.graph_edges import (
    BelongsTo,
    Calls,
    Fk,
    HasColumn,
    HasExample,
    HasMember,
    HasRule,
    ParentOf,
    ReadsWrites,
    StatementNext,
)
from graph.schema.graph_nodes import (
    CodeNode,
    Column,
    Example,
    Module,
    Package,
    Rule,
    Table,
)
from pipeline.models.external_reference_resolution import (
    ExternalReferenceResolutionLedger,
    ResolvedExternalReferenceLink,
)
from pipeline.models.external_source import ExternalSourceRecord
from pipeline.models.human_glossary import HumanGlossary
from pipeline.publication.analysis_health import build_analysis_health_write_batch
from pipeline.publication.components import build_components_write_batch
from pipeline.publication.data_object_meanings import (
    build_data_object_meanings_write_batch,
)
from pipeline.publication.external_chunks import build_external_chunks_write_batch
from pipeline.publication.function_meanings import build_function_meanings_write_batch
from pipeline.publication.glossary import build_glossary_write_batch
from pipeline.publication.module_summary_rollups import (
    build_module_summary_rollups_write_batch,
)
from pipeline.publication.rule_examples import (
    build_examples_write_batch,
    build_rule_meanings_write_batch,
)
from pipeline.publication.search_segments import build_search_segments_write_batch
from pipeline.analysis_ir.parser_facts.source_ast_closure import SourceAstClosure
from pipeline.structure.call_links import build_call_links_write_batch
from pipeline.structure.code_structure import build_code_structure_write_batch
from pipeline.structure.data_access_links import build_data_access_links_write_batch
from pipeline.structure.data_schema_structure import (
    build_data_schema_structure_write_batch,
)


@dataclass(frozen=True)
class CompleteProductPlanInput:
    """All final, already-produced values required by the product graph."""

    analysis_scope_id: str
    generation_id: str
    source_ast_closure: SourceAstClosure
    human_glossary: HumanGlossary

    packages: Sequence[Package]
    modules: Sequence[Module]
    code_nodes: Sequence[CodeNode]
    belongs_to_edges: Sequence[BelongsTo]
    has_member_edges: Sequence[HasMember]
    parent_of_edges: Sequence[ParentOf]
    statement_next_edges: Sequence[StatementNext]

    tables: Sequence[Table]
    columns: Sequence[Column]
    has_column_edges: Sequence[HasColumn]
    foreign_key_edges: Sequence[Fk]

    external_source_records: Sequence[ExternalSourceRecord]
    resolved_external_reference_links: Sequence[ResolvedExternalReferenceLink]
    external_reference_ledger: ExternalReferenceResolutionLedger | Mapping

    calls_edges: Sequence[Calls]
    call_resolution_ledger: Sequence[Mapping[str, object]]
    reads_writes_edges: Sequence[ReadsWrites]

    rules: Sequence[Rule]
    examples: Sequence[Example]
    has_rule_edges: Sequence[HasRule]
    has_example_edges: Sequence[HasExample]

    component_member_ids_by_component_id: Mapping[str, Sequence[str]]
    search_segments: Sequence[Mapping[str, object]]

    prepublish_wall_time_ms: int
    prepublish_performance_artifact_hash: str


def _unresolved_data_object_count(code_nodes: Sequence[CodeNode]) -> int:
    """Count explicit unresolved usage occurrences without inventing graph nodes."""
    return sum(
        len((node.extra or {}).get("unresolved_data_object_usages") or ())
        for node in code_nodes
    )


def build_complete_product_write_plan(
    product_input: CompleteProductPlanInput,
) -> ProductWritePlan:
    """Build all required stages and reject an incomplete stage population."""
    scope = product_input.analysis_scope_id
    generation = product_input.generation_id

    code_structure = build_code_structure_write_batch(
        analysis_scope_id=scope,
        generation_id=generation,
        source_ast_closure=product_input.source_ast_closure,
        packages=product_input.packages,
        modules=product_input.modules,
        code_nodes=product_input.code_nodes,
        belongs_to_edges=product_input.belongs_to_edges,
        has_member_edges=product_input.has_member_edges,
        parent_of_edges=product_input.parent_of_edges,
        statement_next_edges=product_input.statement_next_edges,
    )
    data_schema_structure = build_data_schema_structure_write_batch(
        analysis_scope_id=scope,
        generation_id=generation,
        tables=product_input.tables,
        columns=product_input.columns,
        has_column_edges=product_input.has_column_edges,
        foreign_key_edges=product_input.foreign_key_edges,
    )
    external_chunks = build_external_chunks_write_batch(
        analysis_scope_id=scope,
        generation_id=generation,
        code_nodes=product_input.code_nodes,
        source_records=product_input.external_source_records,
        resolved_links=product_input.resolved_external_reference_links,
        external_reference_ledger=product_input.external_reference_ledger,
    )
    call_links = build_call_links_write_batch(
        analysis_scope_id=scope,
        generation_id=generation,
        code_nodes=product_input.code_nodes,
        calls_edges=product_input.calls_edges,
        call_resolution_ledger=product_input.call_resolution_ledger,
    )
    data_access_links = build_data_access_links_write_batch(
        analysis_scope_id=scope,
        generation_id=generation,
        code_nodes=product_input.code_nodes,
        tables=product_input.tables,
        reads_writes_edges=product_input.reads_writes_edges,
    )
    function_meanings = build_function_meanings_write_batch(
        analysis_scope_id=scope,
        generation_id=generation,
        code_nodes=product_input.code_nodes,
        reads_writes_edges=product_input.reads_writes_edges,
    )
    table_meanings = build_data_object_meanings_write_batch(
        analysis_scope_id=scope,
        generation_id=generation,
        tables=product_input.tables,
        columns=product_input.columns,
    )
    rule_meanings = build_rule_meanings_write_batch(
        analysis_scope_id=scope,
        generation_id=generation,
        code_nodes=product_input.code_nodes,
        rules=product_input.rules,
        has_rule_edges=product_input.has_rule_edges,
    )
    examples = build_examples_write_batch(
        analysis_scope_id=scope,
        generation_id=generation,
        rules=product_input.rules,
        examples=product_input.examples,
        has_example_edges=product_input.has_example_edges,
    )
    module_rollups = build_module_summary_rollups_write_batch(
        analysis_scope_id=scope,
        generation_id=generation,
        modules=product_input.modules,
        code_nodes=product_input.code_nodes,
    )
    components = build_components_write_batch(
        analysis_scope_id=scope,
        generation_id=generation,
        code_nodes=product_input.code_nodes,
        member_ids_by_component_id=(
            product_input.component_member_ids_by_component_id
        ),
    )
    glossary = build_glossary_write_batch(
        analysis_scope_id=scope,
        generation_id=generation,
        glossary=product_input.human_glossary,
    )
    search_segments = build_search_segments_write_batch(
        analysis_scope_id=scope,
        generation_id=generation,
        segments=product_input.search_segments,
    )
    unresolved_calls = sum(
        1
        for row in product_input.call_resolution_ledger
        if row.get("outcome") == "unresolved"
    )
    analysis_health = build_analysis_health_write_batch(
        analysis_scope_id=scope,
        generation_id=generation,
        missing_total=0,
        duplicate_total=0,
        unresolved_call_count=unresolved_calls,
        unresolved_data_object_count=_unresolved_data_object_count(
            product_input.code_nodes,
        ),
        prepublish_wall_time_ms=product_input.prepublish_wall_time_ms,
        prepublish_performance_artifact_hash=(
            product_input.prepublish_performance_artifact_hash
        ),
    )

    batches = (
        code_structure,
        data_schema_structure,
        external_chunks,
        call_links,
        data_access_links,
        function_meanings,
        table_meanings,
        rule_meanings,
        examples,
        module_rollups,
        components,
        glossary,
        search_segments,
        analysis_health,
    )
    actual_stages = {batch.stage_name for batch in batches}
    if actual_stages != PRODUCT_STAGE_NAMES:
        raise ProductWriteContractError(
            "complete product plan stage population differs from the closed schema: "
            f"missing={sorted(PRODUCT_STAGE_NAMES - actual_stages)}, "
            f"unexpected={sorted(actual_stages - PRODUCT_STAGE_NAMES)}"
        )
    return combine_product_write_batches(
        batches,
        analysis_scope_id=scope,
        generation_id=generation,
    )
