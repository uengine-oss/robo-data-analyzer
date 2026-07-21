"""Step5 deterministic external linker orchestration."""
from __future__ import annotations

import time
from typing import Optional

from langchain_core.runnables import RunnableConfig

from graph.edges.external_links import upsert_external_edges
from graph.edges.reads_writes import upsert_reads_writes
from graph.nodes.external import upsert_external_nodes
from pipeline.execution.graph_delta_stream import emit_edges, emit_message, emit_nodes
from pipeline.execution.step_config import step_config
from pipeline.steps.step5_linking.link_external.linker import link_external
from shared.events.processing_events import step_processing_scope
from shared.events.runtime_logger import log_info


_STEP = "step5.link_external"


async def link_external_step(state: dict, config: Optional[RunnableConfig] = None) -> dict:
    """Persist exact external facts and explicit unresolved observations."""
    if any(key not in state for key in ("code_nodes", "has_member_edges", "external_chunks")):
        raise RuntimeError("step5.link_external requires completed step2 and step3 state")
    cfg = step_config(config)
    stream = cfg.get("stream")
    neo = cfg.get("neo_client")
    pause = cfg.get("pause")
    started = time.monotonic()

    async def gate() -> None:
        if pause is not None:
            await pause.gate()

    with step_processing_scope(_STEP, stream):
        code_nodes = state.get("code_nodes") or []
        code_ids = {node.id for node in code_nodes}
        routine_ids = {
            edge.child_id for edge in (state.get("has_member_edges") or [])
            if edge.child_id in code_ids
        }
        outcome = link_external(
            code_nodes=code_nodes,
            modules=state.get("modules") or [],
            variables=state.get("variables") or [],
            constants=state.get("constants") or [],
            chunks=state.get("external_chunks") or [],
            document_nodes=state.get("document_nodes") or [],
            tables=state.get("tables") or [],
            routine_ids=routine_ids,
        )
    await gate()
    if neo is not None:
        await upsert_external_nodes(neo, outcome.unresolved_refs)
        await gate()
        await upsert_external_edges(neo, outcome.external_edges)
        await gate()
        await upsert_reads_writes(neo, outcome.reads_writes_edges)

    await emit_nodes(stream, outcome.unresolved_refs)
    await emit_edges(stream, outcome.external_edges)
    await emit_edges(stream, outcome.reads_writes_edges)
    await gate()
    emit_message(
        stream,
        f"✅ 외부 파일 연결 완료 — 직접/FQN {len(outcome.external_edges)}, 테이블 {len(outcome.reads_writes_edges)}, 미해결 {len(outcome.unresolved_refs)}",
    )
    log_info(
        _STEP,
        "done",
        external_edges=len(outcome.external_edges),
        reads_writes_edges=len(outcome.reads_writes_edges),
        unresolved_refs=len(outcome.unresolved_refs),
        elapsed_sec=f"{time.monotonic() - started:.2f}",
    )
    return {
        "external_edges": outcome.external_edges,
        "reads_writes_edges": outcome.reads_writes_edges,
        "unresolved_refs": outcome.unresolved_refs,
    }
