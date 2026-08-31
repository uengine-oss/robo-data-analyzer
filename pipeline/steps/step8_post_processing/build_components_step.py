"""Deterministically group user routines into product Components.

This step reads only the complete CodeNode/CALLS state.  It performs no LLM
request and no graph write.  The returned membership is later validated and
published by ``pipeline.publication.components`` with every other product
stage, so a failed run cannot expose a partial component view.
"""
from __future__ import annotations

import asyncio
import time
from typing import Optional

from langchain_core.runnables import RunnableConfig

from graph.schema.graph_edges import Calls
from graph.schema.graph_nodes import CodeNode
from pipeline.execution.pause_controller import PauseController
from pipeline.execution.step_config import step_config
from pipeline.languages import ROUTINE_LABELS
from pipeline.analysis_ir.resolution.verified_call_edges import (
    verified_user_routine_call_pairs,
)
from shared.events.processing_events import step_processing_scope
from shared.events.runtime_logger import log_info


_STEP = "step8.build_components"
MIN_COMPONENT_SIZE = 2
LEIDEN_SEED = 42


async def build_components_step(
    state: dict,
    config: Optional[RunnableConfig] = None,
) -> dict:
    """Return the complete deterministic component membership population."""
    cfg = step_config(config)
    stream = cfg.get("stream")
    pause: PauseController = cfg.get("pause") or PauseController()
    code_nodes: list[CodeNode] = state.get("code_nodes", [])
    calls: list[Calls] = state.get("calls_edges", [])
    if not code_nodes:
        log_info(_STEP, "skip", reason="code_nodes 0")
        return {"component_member_ids_by_component_id": {}}

    started = time.monotonic()
    with step_processing_scope(_STEP, stream):
        # Leiden is one deterministic native calculation, not a restartable item
        # fan-out. Keep the event loop responsive without duplicating the work when
        # pause is requested; the post-call gate observes pause/stop before publish.
        await pause.gate()
        clusters = await asyncio.to_thread(_cluster, code_nodes, calls)
        await pause.gate()
    membership = {
        component_id: list(member_ids)
        for component_id, member_ids in clusters
    }
    log_info(
        _STEP,
        "done",
        components=len(membership),
        members=sum(len(member_ids) for member_ids in membership.values()),
        elapsed_sec=f"{time.monotonic() - started:.2f}",
    )
    return {"component_member_ids_by_component_id": membership}


def _cluster(
    code_nodes: list[CodeNode],
    calls: list[Calls],
) -> list[tuple[str, list[str]]]:
    """Return stable Leiden memberships from verified user-routine CALLS."""
    ids = sorted(
        node.id
        for node in code_nodes
        if node.label in ROUTINE_LABELS
        and str(getattr(node, "stereotype", "") or "") not in {"Util", "Utility"}
    )
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate CodeNode IDs prevent deterministic components")
    eligible = set(ids)
    edges = sorted({
        (source_id, target_id)
        for source_id, target_id in verified_user_routine_call_pairs(code_nodes, calls)
        if source_id != target_id
        and source_id in eligible
        and target_id in eligible
    })
    if not edges:
        return []

    try:
        import igraph as ig
        import leidenalg
    except ImportError as error:
        raise RuntimeError(
            "component clustering requires the declared leidenalg and igraph dependencies"
        ) from error

    graph = ig.Graph(directed=False)
    graph.add_vertices(ids)
    graph.add_edges(edges)
    partition = leidenalg.find_partition(
        graph,
        leidenalg.ModularityVertexPartition,
        seed=LEIDEN_SEED,
    )

    components: list[tuple[str, list[str]]] = []
    for community in partition:
        member_ids = sorted(ids[index] for index in community)
        if len(member_ids) < MIN_COMPONENT_SIZE:
            continue
        components.append((f"COMPONENT:{member_ids[0]}", member_ids))
    return sorted(components)
