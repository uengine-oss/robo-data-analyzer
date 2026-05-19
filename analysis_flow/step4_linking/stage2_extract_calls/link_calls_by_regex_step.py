"""stage2_extract_calls (DBMS) — PL/SQL Call regex (LLM 0회).

``CallRegexLinker`` 가 PKG.FUNC + BARE FUNC 추출 → ``Calls`` 엣지.
``DBMS_/UTL_/SYS_`` 내장 패키지는 external 로 분류.
"""
from __future__ import annotations

import time
from typing import Optional

from langchain_core.runnables import RunnableConfig

from analysis_flow.step4_linking.stage2_extract_calls.call_regex_linker import CallRegexLinker
from shared.logging_and_stream.logger import log_info, log_warning
from shared.neo4j.edges.calls import upsert_calls
from shared.neo4j.neo4j_client import Neo4jClient

_STEP = "step4.link_calls_by_regex"


async def link_calls_by_regex_step(state: dict, config: Optional[RunnableConfig] = None) -> dict:
    cfg = (config or {}).get("configurable", {}) if isinstance(config, dict) else {}
    project = cfg.get("project")
    neo: Neo4jClient | None = cfg.get("neo_client")
    if project is None:
        log_warning(_STEP, "config_missing", "project 누락 — step skip")
        return {}

    functions_in = state.get("functions") or []
    started = time.monotonic()
    log_info(_STEP, "start", project=project.name, functions=len(functions_in))

    linker = CallRegexLinker()
    edges = await linker.link(project=project, functions=functions_in)

    # external 엣지는 callee Function 노드가 없을 수 있음 → Neo4j MATCH 실패.
    # internal 만 업서트 (external 은 메타로만 분류).
    internal_edges = [e for e in edges if e.scope == "internal"]
    if neo is not None and internal_edges:
        await upsert_calls(neo, internal_edges)

    log_info(
        _STEP, "done",
        calls_edges=len(edges),
        internal=len(internal_edges), external=len(edges) - len(internal_edges),
        elapsed_sec=f"{time.monotonic() - started:.2f}",
    )
    return {"calls_edges": edges}
