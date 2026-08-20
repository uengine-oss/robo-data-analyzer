"""Prompt values and node-key mapping for DBMS analysis batches."""
from __future__ import annotations

import json
from typing import Optional

from graph.schema.graph_nodes import CodeNode
from pipeline.models.analysis_context import AnalysisContext, dump_external_evidence, dump_referenced_symbols
from pipeline.steps.step7_analyze_code.batch_planner import Batch
from pipeline.steps.step7_analyze_code.range_body import (
    ancestor_context,
    body_for,
    model_visible_raw_body,
)
from pipeline.steps.step7_analyze_code.table_prompt_context import dump_table_refs
from pipeline.steps.step7_analyze_code.control_prompt import (
    build_control_inventory,
    routine_inventory,
)
from pipeline.steps.step7_analyze_code.model_source_projection import (
    project_model_source_literals,
)
from pipeline.models.data_object_usage import dump_data_object_usages, exact_direct_table_ids
from pipeline.models.semantic_frame import profile_prompt_json, slot_specs_json
from pipeline.steps.step7_analyze_code.semantic_frame_planner import (
    build_code_semantic_plan,
    semantic_concern_slots,
)
from pipeline.steps.step7_analyze_code.batch_node_identity import batch_node_keys
from pipeline.languages import ROUTINE_LABELS
from shared.files.cache import SourceFileCache


def _build_batch_values(
    batch: Batch,
    contexts: dict[str, AnalysisContext],
    code_by_id: dict[str, CodeNode],
    codebase_notes: str,
    table_descriptions: dict[str, dict],
    cache: SourceFileCache,
    body_fn=body_for,
    *,
    include_column_usage: bool = False,
) -> tuple[dict, dict[str, str]]:
    """배치 → batch yaml user_values + ``node_key -> code_id`` 매핑.

    프로시저 정보(루트)·조상 골격은 배치 1회 공유(prefix 캐시). 각 형제는 줄범위 key +
    reads/writes/참조심볼 + body. body 는 ``body_fn`` — 기본 ``body_for``(예산 내 원문,
    초과 시 compact). 게이트 규칙 콜만 ``skeleton_body``(자식 접힘 — 자기 줄 조건만).
    """
    first_ctx = contexts[batch.node_ids[0]]
    root = _root_code_node(batch.node_ids[0], contexts, code_by_id)
    parent_ctx = contexts.get(batch.parent_id) if batch.parent_id else None
    ancestor = ancestor_context(first_ctx, contexts, cache) if parent_ctx is not None else "(없음)"

    blocks: list[str] = []
    key_to_id = batch_node_keys(batch.node_ids, contexts)
    id_to_key = {node_id: key for key, node_id in key_to_id.items()}
    for nid in batch.node_ids:
        ctx = contexts[nid]
        key = id_to_key[nid]
        body = project_model_source_literals(body_fn(ctx, contexts, cache))
        fn = code_by_id[nid]
        inventory = build_control_inventory(fn, ctx, cache)
        if fn.label in ROUTINE_LABELS or "rule_rows" not in ctx.extra:
            routine, semantic_inventory = fn, inventory
        else:
            routine, semantic_inventory = routine_inventory(
                fn, contexts, code_by_id, cache,
            )
        assigned_rows = ctx.extra.get("rule_rows")
        profile, slot_specs = build_code_semantic_plan(
            fn,
            ctx,
            inventory=semantic_inventory,
            # The batch scheduler is the authority for rule ownership.  A root
            # routine must not require slots that were delegated to child-node
            # batches, just as a child must not claim its siblings' rows.
            rule_rows=tuple(assigned_rows or ()),
            rule_owner_id=routine.id,
            child_nodes=(
                node for node in code_by_id.values() if node.parent_id == fn.id
            ),
        )
        concerns = semantic_concern_slots(slot_specs)
        ctx.extra["_semantic_frame_plan"] = (profile, slot_specs, concerns)
        branch_index = inventory.branch_index()
        # The validator sees the same authoritative comparison-bearing inputs as
        # the model. Keep this beside prompt assembly so later consumers cannot
        # accidentally validate against hidden compacted child source.
        ctx.extra["summary_comparison_evidence"] = "\n".join((
            model_visible_raw_body(ctx, contexts, cache),
            branch_index,
            fn.expression or "",
        ))
        ctx.extra["return_expressions"] = sorted(inventory.return_expressions)
        excerpt = cache.excerpt(ctx.file_path, ctx.start_line, ctx.end_line)
        callee_summaries = []
        for callee_id in sorted(ctx.callees):
            callee = contexts.get(callee_id)
            if callee is None or not (callee.summary or "").strip():
                continue
            callee_summaries.append({
                "relation": "TARGET_CALLS_CALLEE",
                "callee_id": callee_id,
                "summary": callee.summary.strip(),
                "evidence_status": callee.extra.get("summary_evidence_status") or "sufficient",
                "missing_context": list(callee.extra.get("summary_missing_context") or ()),
            })
        column_usage_block = (
            "column_usage_candidates_json: "
            + json.dumps(
                ctx.extra.get("column_usage_candidates") or {},
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
            if include_column_usage else ""
        )
        blocks.append(
            f"### [TARGET_NODE]\n"
            f"node_key: {key}\n"
            f"target_id: {nid}\n"
            f"file: {ctx.file_path}\n"
            f"source_range: {ctx.start_line}-{ctx.end_line}\n"
            f"coordinate_system: {excerpt.coordinate_system}\n"
            f"source_status: {excerpt.status}\n\n"
            f"#### [STRUCTURAL_PROFILE]\n{profile_prompt_json(profile, concerns['summary'])}\n\n"
            f"#### [APPLICABLE_SLOTS]\n{slot_specs_json(concerns['summary'])}\n\n"
            f"#### [EFFECTIVE_FLOW_EVIDENCE]\n"
            f"ast_type: {fn.label}\n"
            f"ast_expression: {fn.expression}\n"
            f"ast_target: {fn.target}\n"
            f"ast_operator: {fn.operator}\n"
            f"call_target: {fn.name if fn.label == 'CALL' else ''}\n"
            f"effective_reads_json: {dump_table_refs(ctx.reads, table_descriptions, include_columns=False)}\n"
            f"effective_writes_json: {dump_table_refs(ctx.writes, table_descriptions, include_columns=False)}\n"
            f"linked_table_operations_json: "
            f"{json.dumps(ctx.extra.get('table_op') or {}, ensure_ascii=False, sort_keys=True, separators=(',', ':'))}\n"
            f"direct_data_object_usages_json: {dump_data_object_usages(ctx.extra.get('data_object_usages') or ())}\n"
            f"{column_usage_block}"
            f"target_referenced_symbol_definitions_json: {dump_referenced_symbols(ctx.referenced_symbols)}\n"
            f"target_linked_external_context_json: {dump_external_evidence(ctx.external_evidence)}\n\n"
            f"callee_interfaces_json: "
            f"{json.dumps(callee_summaries, ensure_ascii=False, separators=(',', ':'))}\n"
            f"api_contracts_json: "
            f"{json.dumps([dict(item) for item in ctx.extra.get('api_contracts') or ()], ensure_ascii=False, separators=(',', ':'))}\n\n"
            f"#### [CONTROL_FLOW_INDEX]\n"
            f"{project_model_source_literals(branch_index)}\n\n"
            f"#### [SOURCE]\n```\n{body}\n```"
        )
    values = {
        "unit_id": root.id if root else first_ctx.id,
        "unit_signature": root.signature if root else first_ctx.signature,
        "module_intent": "(없음: 파생 prose는 의미 근거로 전달하지 않음)",
        "codebase_notes": codebase_notes or "(없음)",
        "ancestor_context": ancestor,
        "node_count": str(len(batch.node_ids)),
        "node_keys": ", ".join(key_to_id),
        "nodes": "\n\n".join(blocks),
    }
    return values, key_to_id


def build_table_insight_batch_values(
    *,
    batch: Batch,
    contexts: dict[str, AnalysisContext],
    code_by_id: dict[str, CodeNode],
    table_descriptions: dict[str, dict],
    cache: SourceFileCache,
) -> dict[str, str]:
    """Project only exact direct table evidence for the DBMS table concern.

    Summary needs effective descendant flow and surrounding context.  A table-usage item owns
    only the current node's direct access, so sharing the generic summary packet made the model
    infer whether ``reads``/``writes`` were direct.  This projection makes that scope explicit
    and omits ancestor, callee, control-index, and codebase payload that cannot establish direct
    table ownership.
    """
    first_ctx = contexts[batch.node_ids[0]]
    root = _root_code_node(batch.node_ids[0], contexts, code_by_id)
    key_to_id = batch_node_keys(batch.node_ids, contexts)
    id_to_key = {node_id: key for key, node_id in key_to_id.items()}
    blocks: list[str] = []
    for node_id in batch.node_ids:
        context = contexts[node_id]
        node = code_by_id[node_id]
        excerpt = cache.excerpt(
            context.file_path, context.start_line, context.end_line,
        )
        profile, _all_specs, concerns = context.extra["_semantic_frame_plan"]
        blocks.append(
            "### [TABLE_USAGE_TARGET]\n"
            f"node_key: {id_to_key[node_id]}\n"
            f"target_id: {node_id}\n"
            f"file: {context.file_path}\n"
            f"source_range: {context.start_line}-{context.end_line}\n"
            f"coordinate_system: {excerpt.coordinate_system}\n"
            f"source_status: {excerpt.status}\n\n"
            f"structural_profile_json: {profile_prompt_json(profile, concerns['insights'])}\n"
            f"applicable_slots_json: {slot_specs_json(concerns['insights'])}\n\n"
            "#### [EXACT_DIRECT_TABLE_EVIDENCE]\n"
            f"ast_type: {node.label}\n"
            f"ast_expression: {node.expression}\n"
            f"ast_target: {node.target}\n"
            f"ast_operator: {node.operator}\n"
            f"direct_reads_json: "
            f"{dump_table_refs(exact_direct_table_ids(context, 'READ'), table_descriptions, include_columns=False)}\n"
            f"direct_writes_json: "
            f"{dump_table_refs(exact_direct_table_ids(context, 'WRITE'), table_descriptions, include_columns=False)}\n"
            f"direct_table_operations_json: "
            f"{json.dumps(context.extra.get('table_op') or {}, ensure_ascii=False, sort_keys=True, separators=(',', ':'))}\n"
            f"direct_data_object_usages_json: "
            f"{dump_data_object_usages(context.extra.get('data_object_usages') or ())}\n"
            f"column_usage_candidates_json: "
            f"{json.dumps(context.extra.get('column_usage_candidates') or {}, ensure_ascii=False, sort_keys=True, separators=(',', ':'))}\n"
            f"target_referenced_symbol_definitions_json: "
            f"{dump_referenced_symbols(context.referenced_symbols)}\n"
            f"target_linked_external_context_json: "
            f"{dump_external_evidence(context.external_evidence)}\n\n"
            "#### [SOURCE]\n"
            "각 접두사는 절대 파일 줄번호다. 접두사 숫자와 `|`는 소스 코드가 아니다.\n"
            f"```\n{body_for(context, contexts, cache)}\n```"
        )
    return {
        "unit_id": root.id if root else first_ctx.id,
        "unit_signature": root.signature if root else first_ctx.signature,
        "node_count": str(len(batch.node_ids)),
        "node_keys": ", ".join(key_to_id),
        "table_usage_nodes": "\n\n".join(blocks),
    }


def _root_code_node(
    node_id: str, contexts: dict[str, AnalysisContext], code_by_id: dict[str, CodeNode],
) -> Optional[CodeNode]:
    """노드의 프로시저 루트 CodeNode — 유효 parent_id 체인 최상위."""
    cur = node_id
    seen: set[str] = set()
    while cur not in seen:
        seen.add(cur)
        ctx = contexts.get(cur)
        pid = ctx.extra.get("parent_id") if ctx else None
        if pid is None:
            break
        cur = pid
    return code_by_id.get(cur)
