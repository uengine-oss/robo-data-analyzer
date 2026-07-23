"""Step5 deterministic external linker orchestration (spec 078)."""
from __future__ import annotations

import time
from typing import Optional

from langchain_core.runnables import RunnableConfig

from graph.edges.external_links import upsert_external_edges
from pipeline.execution.graph_delta_stream import emit_edges, emit_message
from pipeline.execution.step_config import step_config
from pipeline.steps.step5_linking.link_external.linker import link_external
from shared.events.processing_events import step_processing_scope
from shared.events.runtime_logger import log_info


_STEP = "step5.link_external"


async def link_external_step(state: dict, config: Optional[RunnableConfig] = None) -> dict:
    """Persist exact ``REFER_TO`` facts; unlinked chunks stay normal data."""
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
            variables=state.get("variables") or [],
            constants=state.get("constants") or [],
            chunks=state.get("external_chunks") or [],
            routine_ids=routine_ids,
            project_files=state.get("project_files") or [],
            references_edges=state.get("references_edges") or [],
            init_by_edges=state.get("init_by_edges") or [],
        )
    await gate()
    if neo is not None:
        await upsert_external_edges(neo, outcome.external_edges)

    await emit_edges(stream, outcome.external_edges)
    await gate()
    emit_message(
        stream,
        f"✅ 외부 파일 연결 완료 — REFER_TO {len(outcome.external_edges)}"
        f" · 키가 동적이라 포기 {outcome.dynamic_key_sites}"
        f" · 아무도 안 부르는 조각 {len(outcome.orphan_chunks)}",
    )
    log_info(
        _STEP,
        "done",
        external_edges=len(outcome.external_edges),
        # 아래 셋은 그래프에 넣지 않는다 — 다음에 무엇을 확장할지 판단하는 관측치다.
        dynamic_key_sites=outcome.dynamic_key_sites,
        orphan_chunks=len(outcome.orphan_chunks),
        promoted_wrappers=sorted(outcome.promoted_wrappers),
        elapsed_sec=f"{time.monotonic() - started:.2f}",
    )
    return {"external_edges": outcome.external_edges}
