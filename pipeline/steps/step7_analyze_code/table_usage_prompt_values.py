"""Prompt values and node-key mapping for DBMS table-usage meaning batches."""
from __future__ import annotations

import json

from pipeline.models.analysis_context import AnalysisContext
from pipeline.models.data_object_usage import exact_direct_table_ids
from pipeline.models.function_table_usage_response_schema import (
    function_table_usage_prompt_assignments,
)
from pipeline.steps.step7_analyze_code.batch_node_identity import batch_node_keys
from pipeline.steps.step7_analyze_code.batch_planner import Batch
from pipeline.steps.step7_analyze_code.table_prompt_context import (
    dump_linked_table_descriptions,
)
from shared.files.cache import SourceFileCache


def build_table_usage_meaning_batch_values(
    *,
    batch: Batch,
    contexts: dict[str, AnalysisContext],
    table_descriptions: dict[str, dict],
    cache: SourceFileCache,
) -> dict[str, str]:
    """Build one compact packet per DBMS node from its assigned usage slots."""
    key_to_id = batch_node_keys(batch.node_ids, contexts)
    id_to_key = {node_id: key for key, node_id in key_to_id.items()}
    packets: list[dict] = []
    for node_id in batch.node_ids:
        context = contexts[node_id]
        excerpt = cache.excerpt(
            context.file_path, context.start_line, context.end_line,
        )
        profile, _all_specs, concerns = context.extra["_semantic_frame_plan"]
        usage_specs = concerns["table_usage_meanings"]
        direct_reads = exact_direct_table_ids(context, "READ")
        direct_writes = exact_direct_table_ids(context, "WRITE")
        direct_tables = tuple(dict.fromkeys((*direct_reads, *direct_writes)))
        packets.append({
            "node_key": id_to_key[node_id],
            "assigned_table_usages": function_table_usage_prompt_assignments(
                profile, usage_specs, direct_reads, direct_writes,
            ),
            "linked_table_contexts": json.loads(
                dump_linked_table_descriptions(direct_tables, table_descriptions)
            ),
            "numbered_source": excerpt.numbered_text,
        })
    return {
        "table_usage_packets": json.dumps(
            packets,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ),
    }
