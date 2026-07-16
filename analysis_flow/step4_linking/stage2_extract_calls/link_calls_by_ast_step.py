"""stage2_extract_calls (DBMS) — AST CALL 노드 → ``Calls`` 엣지 (LLM 0).

``CallLinker`` 가 각 CALL 노드를 호출 대상에 직접 잇는다(출발 = CALL 노드, 빌트인 드롭).
internal 은 기존 노드에 연결, external 은 ``:FUNCTION:EXTERNAL`` stub 노드를 스키마·모듈과
함께 생성해 연결 — 둘 다 ``upsert_calls`` 가 처리 (Framework ``link_calls_by_name`` 과 동일).
"""
from __future__ import annotations

import time
from typing import Optional

from langchain_core.runnables import RunnableConfig

from shared.workflow.step_utils import step_config

from analysis_flow.step4_linking.stage2_extract_calls.call_linker import CallLinker
from shared.log.logger import log_info, log_warning
from graph.edges.calls import upsert_calls, emit_calls
from graph.neo4j_client import Neo4jClient
from shared.workflow.graph_stream import emit_message

_STEP = "step4.link_calls_by_ast"


async def link_calls_by_ast_step(state: dict, config: Optional[RunnableConfig] = None) -> dict:
    cfg = step_config(config)
    project = cfg.get("codebase")
    neo: Neo4jClient | None = cfg.get("neo_client")
    if project is None:
        log_warning(_STEP, "config_missing", "project 누락 — step skip")
        return {}

    code_nodes_in = state.get("code_nodes") or []
    started = time.monotonic()
    log_info(_STEP, "start", root=str(project.root), code_nodes=len(code_nodes_in))

    edges = await CallLinker().link(code_nodes=code_nodes_in)

    # Framework(link_calls_by_name)와 동일: 전체 엣지를 upsert. internal 은 기존 노드 연결,
    # external 은 :FUNCTION:EXTERNAL stub(+모듈+스키마) 생성·연결 (upsert_calls 가 분기).
    if neo is not None and edges:
        await upsert_calls(neo, edges)

    # 라이브 스트림 — 호출 관계를 step 완료 즉시 화면으로. external 은 stub 노드까지 함께
    # 발신(emit_calls = upsert_calls 와 동일 그래프) → 라이브=neo4j (외부호출 끝점 일치).
    stream = cfg.get("stream")
    emit_calls(stream, edges)
    emit_message(stream, f"✅ 호출 링킹 완료 — {len(edges)}건")

    internal = sum(1 for e in edges if e.scope == "internal")
    log_info(
        _STEP, "done",
        calls_edges=len(edges),
        internal=internal, external=len(edges) - internal,
        elapsed_sec=f"{time.monotonic() - started:.2f}",
    )
    return {"calls_edges": edges}
