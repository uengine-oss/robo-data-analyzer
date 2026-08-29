"""Resolve Parser call arguments to verified external-source records."""
from __future__ import annotations

import time
from typing import Optional

from langchain_core.runnables import RunnableConfig

from pipeline.execution.graph_delta_stream import emit_message
from pipeline.execution.step_config import step_config
from pipeline.steps.step5_linking.link_external.linker import link_external
from shared.events.processing_events import step_processing_scope
from shared.events.runtime_logger import log_info


_STEP = "step5.link_external"


async def link_external_step(state: dict, config: Optional[RunnableConfig] = None) -> dict:
    if any(
        key not in state for key in ("call_sites", "external_source_records")
    ):
        raise RuntimeError("step5.link_external requires completed step2 and step3 state")
    cfg = step_config(config)
    stream = cfg.get("stream")
    pause = cfg.get("pause")
    started = time.monotonic()

    async def gate() -> None:
        if pause is not None:
            await pause.gate()

    with step_processing_scope(_STEP, stream):
        outcome = link_external(
            call_sites=state.get("call_sites") or [],
            constants=state.get("constants") or [],
            source_records=state.get("external_source_records") or [],
            source_files=state.get("external_source_files") or [],
            references_edges=state.get("references_edges") or [],
        )
    await gate()
    ledger = outcome.external_reference_ledger
    emit_message(
        stream,
        "✅ 외부 원문 연결 확인 완료 — "
        f"제품 관계 후보 {len(outcome.resolved_external_reference_links)}"
        f" · 호출 인자 회계 {len(ledger.occurrences)}",
    )
    log_info(
        _STEP,
        "done",
        resolved_external_reference_links=len(
            outcome.resolved_external_reference_links
        ),
        external_reference_occurrences=len(ledger.occurrences),
        elapsed_sec=f"{time.monotonic() - started:.2f}",
    )
    return {
        "resolved_external_reference_links": (
            outcome.resolved_external_reference_links
        ),
        "external_reference_ledger": ledger.model_dump(mode="json"),
    }
