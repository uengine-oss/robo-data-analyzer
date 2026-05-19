"""stage1_regex_grep — ``ProfileResult.regex`` 로 코드 grep → R/W 엣지.

LLM 0회 / Agent 0회. 매칭 안 된 케이스는 ``unresolved_cases`` 큐에 누적
(stage3_resolve_unresolved 가 처리, Framework 전용).
"""
from __future__ import annotations

import time
from typing import Optional

from langchain_core.runnables import RunnableConfig

from analysis_flow.step4_linking.stage1_regex_grep.regex_table_linker import RegexTableLinker
from shared.logging_and_stream.logger import log_info, log_warning
from shared.neo4j.edges.reads_writes import upsert_reads_writes
from shared.neo4j.neo4j_client import Neo4jClient

_STEP = "step4.link_deterministic"


async def link_deterministic_step(state: dict, config: Optional[RunnableConfig] = None) -> dict:
    """결정론 grep → ``ReadsWrites`` 엣지 + ``unresolved`` 큐."""
    cfg = (config or {}).get("configurable", {}) if isinstance(config, dict) else {}
    project = cfg.get("project")
    neo: Neo4jClient | None = cfg.get("neo_client")
    if project is None:
        log_warning(_STEP, "config_missing", "project 누락 — step skip")
        return {}

    profile = state.get("profile_result")
    patterns = (profile or {}).get("table_access_patterns") or []
    functions_in = state.get("functions") or []
    tables_in = state.get("tables") or []
    started = time.monotonic()
    log_info(
        _STEP, "start",
        project=project.name,
        patterns=len(patterns), functions=len(functions_in), tables=len(tables_in),
    )

    linker = RegexTableLinker()
    edges, unresolved = await linker.link(
        project=project,
        functions=functions_in,
        tables=tables_in,
        profile_result=profile,
    )

    if neo is not None and edges:
        await upsert_reads_writes(neo, edges)

    log_info(
        _STEP, "done",
        reads_writes_edges=len(edges), unresolved_cases=len(unresolved),
        elapsed_sec=f"{time.monotonic() - started:.2f}",
    )

    return {
        "reads_writes_edges": edges,
        "unresolved_cases": unresolved,
    }
