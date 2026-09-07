"""Prompt values and node-key mapping for DBMS table-usage meaning batches."""
from __future__ import annotations

import json

from pipeline.models.analysis_context import AnalysisContext
from pipeline.models.data_object_usage import exact_direct_table_ids
from pipeline.meaning.table.response_schema import (
    function_table_usage_prompt_assignments,
)
from pipeline.meaning.batch_node_identity import batch_node_keys
from pipeline.meaning.batch_planner import Batch
from pipeline.meaning.table.source_projection import render_table_source
from graph.schema.graph_nodes import CodeNode
from shared.files.source_cache import SourceFileCache


def build_table_usage_meaning_batch_values(
    *,
    batch: Batch,
    contexts: dict[str, AnalysisContext],
    code_by_id: dict[str, CodeNode],
    table_descriptions: dict[str, dict],
    cache: SourceFileCache,
) -> dict[str, str]:
    """Build one compact packet per DBMS node from its assigned usage slots."""
    key_to_id = batch_node_keys(batch.node_ids)
    id_to_key = {node_id: key for key, node_id in key_to_id.items()}
    packets: dict[str, dict] = {}
    for node_id in batch.node_ids:
        context = contexts[node_id]
        profile, _all_specs, concerns = context.extra["_semantic_frame_plan"]
        usage_specs = concerns["table_usage_meanings"]
        direct_reads = exact_direct_table_ids(context, "READ")
        direct_writes = exact_direct_table_ids(context, "WRITE")
        packets[id_to_key[node_id]] = {
            "assigned_table_usages": function_table_usage_prompt_assignments(
                profile, usage_specs, direct_reads, direct_writes,
                table_descriptions,
            ),
            "relevant_source": render_table_source(context, code_by_id, cache),
        }
    return {
        # Recorder identity only. The prompt template does not reference this value.
        "target_id": batch.parent_id or batch.node_ids[0],
        "table_usage_packets": json.dumps(
            packets,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ),
    }
