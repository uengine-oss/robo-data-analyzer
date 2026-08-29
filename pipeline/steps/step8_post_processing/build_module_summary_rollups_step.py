"""Build deterministic Module rollups from already-validated function summaries.

Each CodeNode receives its Module identity once in the AST loading stage as
``owner_id``. This step follows that identity directly; it does not infer membership
again from paths or line ranges. A complete Module rollup is the byte-identical pair
of sorted function IDs and their existing summaries. It creates no new prose,
stereotype, logical name, semantic frame, or LLM request.
"""
from __future__ import annotations

import time
from collections import defaultdict
from dataclasses import replace
from typing import Optional

from langchain_core.runnables import RunnableConfig

from graph.schema.graph_nodes import CodeNode, Module
from pipeline.execution.analysis_handoff import AnalysisHandoffAborted, handoff_of
from pipeline.execution.pause_controller import PauseController
from pipeline.execution.quality_finding_log import record_analysis_findings
from pipeline.execution.step_config import step_config
from pipeline.languages import ROUTINE_LABELS
from pipeline.models.analysis_finding import AnalysisFinding
from pipeline.steps.step8_post_processing.handoff_skip import log_handoff_skip
from shared.events.processing_events import step_processing_scope
from shared.events.runtime_logger import log_info, log_warning


_STEP = "step8.build_module_summary_rollups"


async def build_module_summary_rollups_step(
    state: dict,
    config: Optional[RunnableConfig] = None,
) -> dict:
    """Wait only for function summaries, then publish deterministic Module pairs."""
    cfg = step_config(config)
    stream = cfg.get("stream")
    pause: PauseController = cfg.get("pause") or PauseController()
    handoff = handoff_of(cfg)
    try:
        result = await _build_rollups(state, handoff, stream, pause)
    except AnalysisHandoffAborted:
        log_handoff_skip(_STEP, "모듈 함수 요약 묶음", pause)
        return {}
    except BaseException as error:
        handoff.abort(error)
        raise
    return result


async def _build_rollups(
    state: dict,
    handoff,
    stream,
    pause: PauseController,
) -> dict:
    await handoff.summaries()
    modules: list[Module] = state.get("modules", [])
    code_nodes: list[CodeNode] = handoff.merge(state.get("code_nodes", []))
    if not modules:
        log_info(_STEP, "skip", modules=0, code_nodes=len(code_nodes))
        return {}

    started = time.monotonic()
    functions_by_module = _group_functions_by_module(code_nodes, modules)
    log_info(
        _STEP,
        "start",
        modules=len(modules),
        functions=sum(len(items) for items in functions_by_module.values()),
    )

    def _build_one(module: Module) -> Module:
        functions = functions_by_module.get(module.id, ())
        if not functions:
            return replace(
                module,
                summary_rollup_function_ids=(),
                summary_rollup_summaries=(),
            )

        missing_summary_ids = tuple(
            function.id
            for function in functions
            if not isinstance(function.summary, str) or not function.summary.strip()
        )
        if missing_summary_ids:
            record_analysis_findings([AnalysisFinding(
                concern="module_summary_rollup",
                status="unavailable",
                target_id=module.id,
                reasons=(
                    "모든 소속 함수에 검증된 summary가 있어야 Module rollup을 공개함: "
                    + ", ".join(missing_summary_ids),
                ),
                evidence_rule="deterministic_module_summary_rollup",
            )])
            log_warning(
                _STEP,
                "quality_finding",
                "함수 summary가 빠져 Module rollup 전체를 공개하지 않음",
                module_id=module.id,
                missing_count=len(missing_summary_ids),
            )
            return replace(
                module,
                summary_rollup_function_ids=(),
                summary_rollup_summaries=(),
            )

        return replace(
            module,
            summary_rollup_function_ids=tuple(function.id for function in functions),
            # Emptiness is validated, but the accepted summary bytes are never rewritten.
            summary_rollup_summaries=tuple(function.summary for function in functions),
        )

    async def _run_projection() -> list[Module]:
        projected: list[Module] = []
        for module in modules:
            await pause.gate()
            projected.append(_build_one(module))
        return projected

    with step_processing_scope(_STEP, stream):
        updated = await pause.run_restartable_during_pause(_run_projection)
    completed = sum(bool(module.summary_rollup_function_ids) for module in updated)
    log_info(
        _STEP,
        "done",
        modules=len(modules),
        rollups=completed,
        elapsed_sec=f"{time.monotonic() - started:.2f}",
    )
    return {"modules": updated}


def _group_functions_by_module(
    code_nodes: list[CodeNode],
    modules: list[Module],
) -> dict[str, list[CodeNode]]:
    """Use the AST-produced owner ID and sort each Module's functions by ID."""
    module_ids = {module.id for module in modules}
    if len(module_ids) != len(modules):
        raise ValueError("duplicate Module IDs prevent exact summary rollup ownership")

    functions = [node for node in code_nodes if node.label in ROUTINE_LABELS]
    function_ids = [function.id for function in functions]
    if len(function_ids) != len(set(function_ids)):
        raise ValueError("duplicate function IDs prevent exact Module summary rollup")

    result: dict[str, list[CodeNode]] = defaultdict(list)
    for function in functions:
        if function.owner_id not in module_ids:
            raise ValueError(
                f"{function.id}: AST owner_id does not identify a loaded Module: "
                f"{function.owner_id!r}"
            )
        result[function.owner_id].append(function)
    for members in result.values():
        members.sort(key=lambda function: function.id)
    return result
