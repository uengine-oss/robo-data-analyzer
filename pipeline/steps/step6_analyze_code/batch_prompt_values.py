"""Prompt values and node-key mapping for DBMS analysis batches."""
from __future__ import annotations

from typing import Optional

from graph.schema.graph_nodes import CodeNode
from pipeline.models.analysis_context import AnalysisContext, dump_referenced_symbols
from pipeline.steps.step6_analyze_code.batch_planner import Batch
from pipeline.steps.step6_analyze_code.range_body import ancestor_context, body_for
from pipeline.steps.step6_analyze_code.table_prompt_context import dump_table_refs
from source.file_cache import SourceFileCache


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
        blocks.append(
            f"### 노드 {key}\n"
            f"reads(JSON): {dump_table_refs(ctx.reads, table_descriptions)}\n"
            f"writes(JSON): {dump_table_refs(ctx.writes, table_descriptions)}\n"
            f"참조심볼(JSON): {dump_referenced_symbols(ctx.referenced_symbols)}\n"
            f"body:\n```\n{body}\n```"
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
