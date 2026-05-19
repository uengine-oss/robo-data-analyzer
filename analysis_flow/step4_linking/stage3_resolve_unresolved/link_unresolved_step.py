"""stage3_resolve_unresolved — Framework Linking Agent invoke.

흐름 (case 별 quarantine, Plan §3.6):
1. ``state["unresolved_cases"]`` 의 각 case → ``UnresolvedAgentLinker`` 가 invoke.
2. **case 별 자기 collector** — invoke 안에서 도구가 누적 → case 간 안 섞임.
3. 각 case outcome 의 snapshot 을 합쳐 ``ReadsWrites`` / ``ReferTo`` 엣지 생성.
4. Neo4j 업서트.

``ReferTo`` 의 ``function_id`` 는 도구 ``graph_create_refer_to(function_id, ...)`` 인자로
직접 받음 (collector 가 case caller fqn 보존) — 빈 문자열 박지 X.
"""
from __future__ import annotations

import time
from typing import Optional

from langchain_core.runnables import RunnableConfig

from analysis_flow.step4_linking.stage3_resolve_unresolved.unresolved_agent_linker import (
    UnresolvedAgentLinker,
)
from data_models.graph_edges import ReadsWrites, ReferTo
from shared.logging_and_stream.logger import log_info, log_warning
from shared.neo4j.edges.reads_writes import upsert_reads_writes
from shared.neo4j.edges.refer_to import upsert_refer_to
from shared.neo4j.neo4j_client import Neo4jClient

_STEP = "step4.link_unresolved"


async def link_unresolved_step(state: dict, config: Optional[RunnableConfig] = None) -> dict:
    """unresolved case → Linking Agent → ``ReadsWrites`` / ``ReferTo`` 엣지."""
    cfg = (config or {}).get("configurable", {}) if isinstance(config, dict) else {}
    run = cfg.get("run")
    cost_budget = cfg.get("cost_budget")
    stream = cfg.get("stream")
    neo: Neo4jClient | None = cfg.get("neo_client")

    unresolved = state.get("unresolved_cases", [])
    if not unresolved:
        log_info(_STEP, "skip", reason="no unresolved cases")
        return {}
    if cost_budget is None or run is None:
        log_warning(_STEP, "config_missing", "cost_budget/run 누락 — step skip", unresolved=len(unresolved))
        return {}

    profile_result = state.get("profile_result") or {}
    codebase_notes = profile_result.get("codebase_notes", "")
    started = time.monotonic()
    log_info(_STEP, "start", cases=len(unresolved), has_codebase_notes=bool(codebase_notes))

    linker = UnresolvedAgentLinker()
    outcomes = await linker.link(
        unresolved_cases=unresolved,
        codebase_notes=codebase_notes,
        run_dir=run.run_dir,
        cost_budget=cost_budget,
        neo_client=neo,
        stream_emit=stream.emit if stream is not None else None,
    )

    rw_edges: list[ReadsWrites] = []
    refer_to_edges: list[ReferTo] = []

    for outcome in outcomes:
        snap = outcome.snapshot
        for row in snap.get("table_accesses", []):
            rw_edges.append(ReadsWrites(
                function_id=row["function_id"],
                table_fqn=row["table"],
                relation=row["crud"],
                evidence_file=_evidence_file(row.get("evidence", "")),
                evidence_line=_evidence_line(row.get("evidence", "")),
            ))
        for row in snap.get("refer_tos", []):
            # graph_create_refer_to 가 function_id 인자 받으므로 row 에 정상 채워짐.
            # 도구 호출 시 누락된 경우엔 outcome.function_id (case caller) fallback.
            refer_to_edges.append(ReferTo(
                function_id=row.get("function_id") or outcome.function_id,
                target_type=row["target_type"],
                target_key=row["target_key"],
                evidence_file=_evidence_file(row.get("evidence", "")),
                evidence_line=_evidence_line(row.get("evidence", "")),
            ))

    if neo is not None:
        if rw_edges:
            await upsert_reads_writes(neo, rw_edges)
        if refer_to_edges:
            await upsert_refer_to(neo, refer_to_edges)

    resolved = sum(1 for o in outcomes if getattr(o, "resolved", False))
    log_info(
        _STEP, "done",
        cases=len(unresolved), resolved=resolved, still_unresolved=len(unresolved) - resolved,
        reads_writes_edges=len(rw_edges), refer_to_edges=len(refer_to_edges),
        elapsed_sec=f"{time.monotonic() - started:.2f}",
    )
    return {
        "reads_writes_edges": rw_edges,
        "refer_to_edges": refer_to_edges,
    }


def _evidence_file(evidence: str) -> str:
    """``file:line`` 형식의 ``file`` 부분."""
    if not evidence:
        return ""
    return evidence.split(":", 1)[0]


def _evidence_line(evidence: str) -> int:
    """``file:line`` 형식의 ``line`` 부분 (정수, 실패 시 0)."""
    if not evidence:
        return 0
    parts = evidence.split(":")
    for token in reversed(parts):
        if token.isdigit():
            return int(token)
    return 0
