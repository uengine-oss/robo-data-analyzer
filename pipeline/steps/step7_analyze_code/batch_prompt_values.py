"""Prompt values and node-key mapping for DBMS analysis batches."""
from __future__ import annotations

import json
from typing import Optional

from graph.schema.graph_nodes import CodeNode
from pipeline.models.analysis_context import AnalysisContext, dump_external_evidence, dump_referenced_symbols
from pipeline.steps.step7_analyze_code.batch_planner import Batch
from pipeline.steps.step7_analyze_code.range_body import ancestor_context, body_for
from pipeline.steps.step7_analyze_code.table_prompt_context import dump_table_refs
from pipeline.steps.step7_analyze_code.control_prompt import build_control_inventory
from shared.files.cache import SourceFileCache


def _build_batch_values(
    batch: Batch,
    contexts: dict[str, AnalysisContext],
    code_by_id: dict[str, CodeNode],
    codebase_notes: str,
    module_intents: dict[str, str],
    table_descriptions: dict[str, dict],
    cache: SourceFileCache,
    body_fn=body_for,
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
    key_to_id: dict[str, str] = {}
    for nid in batch.node_ids:
        ctx = contexts[nid]
        key = f"{ctx.start_line}-{ctx.end_line}"
        key_to_id[key] = nid
        body = body_fn(ctx, contexts, cache)
        fn = code_by_id[nid]
        inventory = build_control_inventory(fn, ctx, cache)
        ctx.extra["return_expressions"] = sorted(inventory.return_expressions)
        excerpt = cache.excerpt(ctx.file_path, ctx.start_line, ctx.end_line)
        callee_summaries = []
        for callee_id in sorted(ctx.callees):
            callee = contexts.get(callee_id)
            if callee is None or not (callee.summary or "").strip():
                continue
            callee_summaries.append({
                "callee_id": callee_id,
                "summary": callee.summary.strip(),
            })
        blocks.append(
            f"### [TARGET_NODE]\n"
            f"node_key: {key}\n"
            f"file: {ctx.file_path}\n"
            f"source_range: {key}\n"
            f"coordinate_system: {excerpt.coordinate_system}\n"
            f"source_status: {excerpt.status}\n\n"
            f"#### [DIRECT_EVIDENCE]\n"
            f"reads_json: {dump_table_refs(ctx.reads, table_descriptions, include_columns=False)}\n"
            f"writes_json: {dump_table_refs(ctx.writes, table_descriptions, include_columns=False)}\n"
            f"authoritative_table_ops_json: "
            f"{json.dumps(ctx.extra.get('table_op') or {}, ensure_ascii=False, sort_keys=True, separators=(',', ':'))}\n"
            f"referenced_symbols_json: {dump_referenced_symbols(ctx.referenced_symbols)}\n"
            f"external_evidence_json: {dump_external_evidence(ctx.external_evidence)}\n\n"
            f"callee_summaries_json: "
            f"{json.dumps(callee_summaries, ensure_ascii=False, separators=(',', ':'))}\n"
            f"api_contracts_json: "
            f"{json.dumps([dict(item) for item in ctx.extra.get('api_contracts') or ()], ensure_ascii=False, separators=(',', ':'))}\n\n"
            f"#### [CONTROL_FLOW_INDEX]\n"
            f"{inventory.branch_index()}\n\n"
            f"#### [SOURCE]\n```\n{body}\n```"
        )
    values = {
        "unit_id": root.id if root else first_ctx.id,
        "unit_signature": root.signature if root else first_ctx.signature,
        "module_intent": module_intents.get(root.owner_id if root else "") or "(없음)",
        "codebase_notes": codebase_notes or "(없음)",
        "ancestor_context": ancestor,
        "node_count": str(len(batch.node_ids)),
        "node_keys": ", ".join(key_to_id),
        "nodes": "\n\n".join(blocks),
    }
    return values, key_to_id


def build_rule_batch_values(
    *,
    expected: dict[str, str],
    assignments: dict[str, tuple[int, ...] | list[int]],
    evidences: dict[str, dict[str, object]],
    contexts: dict[str, AnalysisContext],
    code_by_id: dict[str, CodeNode],
    table_descriptions: dict[str, dict],
) -> dict[str, str]:
    """Render only evidence consumed by the DBMS Rule contract.

    Summary, ancestor prose, control indexes and full node SOURCE were previously repeated in
    every concern.  Rule rows already have exact active source in ``rule_evidence_json``; only
    facts outside that row source are added here.
    """
    blocks: list[str] = []
    for node_key, node_id in expected.items():
        ctx = contexts[node_id]
        fn = code_by_id[node_id]
        callee_summaries = []
        for callee_id in sorted(ctx.callees):
            callee = contexts.get(callee_id)
            if callee is None or not (callee.summary or "").strip():
                continue
            callee_summaries.append({
                "callee_id": callee_id,
                "summary": callee.summary.strip(),
            })
        rows = tuple(int(row) for row in assignments[node_key])
        blocks.append(
            f"### [RULE_TARGET {node_key}]\n"
            f"target_id: {node_id}\n"
            f"signature: {fn.signature}\n"
            f"assigned_rows: {','.join(map(str, rows))}\n"
            f"reads_json: {dump_table_refs(ctx.reads, table_descriptions, include_columns=False)}\n"
            f"writes_json: {dump_table_refs(ctx.writes, table_descriptions, include_columns=False)}\n"
            f"authoritative_table_ops_json: "
            f"{json.dumps(ctx.extra.get('table_op') or {}, ensure_ascii=False, sort_keys=True, separators=(',', ':'))}\n"
            f"referenced_symbols_json: {dump_referenced_symbols(ctx.referenced_symbols)}\n"
            f"external_evidence_json: {dump_external_evidence(ctx.external_evidence)}\n"
            f"callee_summaries_json: "
            f"{json.dumps(callee_summaries, ensure_ascii=False, separators=(',', ':'))}\n"
            f"api_contracts_json: "
            f"{json.dumps([dict(item) for item in ctx.extra.get('api_contracts') or ()], ensure_ascii=False, separators=(',', ':'))}\n"
            f"rule_evidence_json: "
            f"{json.dumps(evidences[node_key], ensure_ascii=False, separators=(',', ':'))}"
        )
    return {"rule_nodes": "\n\n".join(blocks)}


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
