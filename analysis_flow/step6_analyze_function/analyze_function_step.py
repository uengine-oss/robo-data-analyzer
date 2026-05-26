"""step6_analyze_function — 모듈 사전분석 + Bottom-up DAG + 3 분할 fan-out.

순서:
0. 모듈 사전분석 (Framework 전용) — 모듈마다 LLM 1회로 헤더 주석 → 모듈 의도.
   결과 ``module_intent`` 는 아래 함수 분석의 3분할 공통 context 에 주입.
1. 호출 그래프 (CALLS + PARENT_OF) → ``find_call_cycles`` → cycle 식별 (Tarjan SCC).
2. ``bottom_up_rounds`` → leaf → root Round 별 정렬.
3. Round 별 병렬 분석:
   - 같은 Round 함수들 ``asyncio.gather`` + ``Semaphore`` 병렬.
   - Round 간 순차 — 다음 Round 의 caller 가 이전 Round 의 callee summary 사용.
4. 각 함수 = ``analyze_function_three_split`` (3 분할 fan-out, RadixAttention 95%+).

cycle 내부 함수 → callee summary 없이 분석 (``is_in_cycle=True`` 메타).
"""
from __future__ import annotations

import asyncio
import time
from typing import Any, Optional

from langchain_core.runnables import RunnableConfig

from shared.source_file_cache import SourceFileCache
from shared.workflow_runner.step_config import step_config

from analysis_flow.step6_analyze_function.analysis_order import bottom_up_rounds
from analysis_flow.step6_analyze_function.call_cycles import find_call_cycles
from analysis_flow.step6_analyze_function.module_pre_analysis import run_module_pre_analysis
from analysis_flow.step6_analyze_function.three_split_llm_fanout import (
    analyze_function_three_split,
    run_summary,
)
from data_models.graph_nodes import Function
from shared.config.env_catalog import ENV
from shared.example_mapping import build_example_mapping, write_example_mapping
from shared.example_mapping.builder import ExampleMappingResult
from shared.llm.cost_budget import BudgetExceededError
from shared.llm.llm_output_models import SummaryOutput
from shared.logging_and_stream.logger import log_error, log_info, log_warning
from shared.logging_and_stream.ndjson_stream import NDJSONStream
from shared.neo4j.neo4j_client import Neo4jClient
from shared.neo4j.nodes.function import update_summaries

_STEP = "step6.analyze_function"


async def analyze_function_step(state: dict, config: Optional[RunnableConfig] = None) -> dict:
    """Bottom-up DAG + 3 분할 fan-out.

    함수 분석 결과는 **함수 단위 즉시** Neo4j 박힘 + NDJSON stream emit — 라운드 또는
    step 끝까지 batch 누적 안 함. 메모리 누적 0, 그래프 실시간 반영.
    """
    cfg = step_config(config)
    strategy = cfg.get("strategy")
    cost_budget = cfg.get("cost_budget")
    neo: Neo4jClient | None = cfg.get("neo_client")
    stream: Optional[NDJSONStream] = cfg.get("stream")

    contexts = state.get("analysis_contexts", {})
    functions_list = state.get("functions", [])
    function_by_id = {f.function_id: f for f in functions_list}

    # 테이블 fqn → DDL 설명 — 3분할 reads/writes 렌더에 의미 부여 (LLM 이 테이블 뜻을 앎).
    table_descriptions = {
        t.fqn: t.description
        for t in state.get("tables", [])
        if t.description
    }

    # ScanResult.codebase_notes — 3분할 모든 호출이 공통 prefix 로 사용 (캐시 친화).
    # Framework 만 채움 — DBMS 는 빈 문자열 (Scan Agent 안 씀).
    scan_result = state.get("scan_result") or {}
    codebase_notes = scan_result.get("codebase_notes", "")

    if not contexts:
        log_info(_STEP, "skip", reason="no analysis_contexts")
        return {}

    started = time.monotonic()

    # 0. 모듈 사전분석 — 모듈당 LLM 1회로 헤더 주석 → 모듈 의도.
    #    DBMS 는 module_pre_analysis.yaml 이 없어 빈 dict 반환 (Framework 전용).
    module_intents = await run_module_pre_analysis(
        state.get("modules", []), functions_list, strategy, cost_budget, neo,
    )

    # 1. cycle 식별 (CALLS + PARENT_OF 둘 다)
    edges: list[tuple[str, str]] = [
        (e.caller_function_id, e.callee_function_id)
        for e in state.get("calls_edges", [])
    ]
    edges.extend(
        (e.parent_function_id, e.child_function_id)
        for e in state.get("parent_of_edges", [])
    )
    all_ids = list(contexts.keys())
    cycles = find_call_cycles(edges, all_ids)

    # cycle 메타 ctx 에 부착
    for fn_id in cycles:
        if fn_id in contexts:
            contexts[fn_id].is_in_cycle = True

    # 2. Bottom-up Round 정렬
    rounds = bottom_up_rounds(edges, all_ids, cycles)
    log_info(
        _STEP, "start",
        functions=len(contexts), rounds=len(rounds), in_cycle=len(cycles),
        concurrency=ENV.pipeline.max_concurrency,
    )

    # 3. Round 별 병렬 분석 — Round 간은 순차. 결과는 함수 단위 즉시 영속화 + emit.
    semaphore = asyncio.Semaphore(ENV.pipeline.max_concurrency)
    source_cache = SourceFileCache()       # 같은 파일을 여러 함수가 1회만 read
    total_analyzed = 0
    total_failed = 0

    for round_idx, round_fn_ids in enumerate(rounds, start=1):
        round_started = time.monotonic()
        log_info(_STEP, "round_start", round=f"{round_idx}/{len(rounds)}", functions=len(round_fn_ids))

        if not round_fn_ids:
            continue

        # === Round 워밍 ===
        # 첫 함수의 #1 summary 만 단독 선호출 → LLM 서버 prefix cache 에 공통지침
        # 올림. 그 후 모든 함수 풀어놓으면 — 각 함수가 자기 흐름 (#1 → #2 #3),
        # 함수 사이 동시 진행. 같은 함수 안만 #1 → #2 #3 순서 (cache 효과 위해).
        first_id = round_fn_ids[0]
        first_summary: Optional[SummaryOutput] = await run_summary(
            first_id, contexts, function_by_id, strategy, cost_budget,
            codebase_notes, module_intents, table_descriptions, source_cache,
        )

        # === 모든 함수 풀어놓음 ===
        # 첫 함수는 워밍 결과 재사용 (precomputed_summary), 나머지는 각자 처음부터.
        # 각 함수 안에서 #1 → #2 #3 흐름은 await + asyncio.gather 가 자동 보장.
        tasks = [
            _analyze_one(
                fn_id, contexts, function_by_id, strategy, cost_budget,
                semaphore, codebase_notes, module_intents, table_descriptions,
                source_cache,
                precomputed_summary=(first_summary if fn_id == first_id else None),
            )
            for fn_id in round_fn_ids
        ]
        # return_exceptions=True 로 받지만 BudgetExceededError 는 즉시 전파.
        # 개별 분석 실패 (Pydantic 파싱 등) 만 로깅 후 건너뜀.
        # 성공 함수는 그 자리에서 즉시 Neo4j 박고 stream emit — batch 누적 X.
        results = await asyncio.gather(*tasks, return_exceptions=True)
        round_failed = 0
        round_analyzed = 0
        for fn_id, r in zip(round_fn_ids, results):
            if isinstance(r, BudgetExceededError):
                raise r          # 절대원칙 #3 — 회로 차단기 즉시 전파
            if isinstance(r, BaseException):
                round_failed += 1
                log_error(_STEP, "round_analysis", r, function_id=fn_id, round=round_idx)
                continue
            if r is None:
                continue
            await _persist_and_emit_function(r, neo, stream)
            round_analyzed += 1

        total_failed += round_failed
        total_analyzed += round_analyzed
        log_info(
            _STEP, "round_done",
            round=f"{round_idx}/{len(rounds)}",
            analyzed=round_analyzed, failed=round_failed or None,
            elapsed_sec=f"{time.monotonic() - round_started:.2f}",
        )

    log_info(
        _STEP, "done",
        functions=len(contexts), analyzed=total_analyzed,
        failed=total_failed or None, in_cycle=len(cycles),
        elapsed_sec=f"{time.monotonic() - started:.2f}",
    )
    return {"analysis_contexts": contexts}


async def _analyze_one(
    fn_id: str,
    contexts: dict,
    function_by_id: dict,
    strategy: Any,
    cost_budget,
    semaphore: asyncio.Semaphore,
    codebase_notes: str,
    module_intents: dict[str, str],
    table_descriptions: dict[str, str],
    source_cache: SourceFileCache,
    precomputed_summary: Optional[SummaryOutput] = None,
):
    async with semaphore:
        return await analyze_function_three_split(
            fn_id, contexts, function_by_id, strategy,
            cost_budget=cost_budget,
            codebase_notes=codebase_notes,
            module_intents=module_intents,
            table_descriptions=table_descriptions,
            source_cache=source_cache,
            precomputed_summary=precomputed_summary,
        )


# ─── 함수 분석 결과 영속화 + 스트림 ────────────────────────────
# 함수 1건 분석 끝나면 즉시 호출 (batch 누적 X). 메모리 누적 0 + 그래프 실시간.

async def _persist_and_emit_function(
    fn: Function,
    neo: Optional[Neo4jClient],
    stream: Optional[NDJSONStream],
) -> None:
    """함수 분석 결과 1건 → Neo4j upsert + NDJSON node/relationship 이벤트.

    동시 호출 X (caller 의 라운드 루프가 순차 처리) — Neo4j 락 경합 없음.
    neo / stream 둘 중 하나라도 None 이면 그 쪽은 skip — 테스트·CLI 모드에서도 안전.
    """
    em = build_example_mapping([fn])
    if neo is not None:
        await update_summaries(neo, [fn])
        await write_example_mapping(neo, em)
    if stream is not None:
        _emit_function_graph(stream, fn, em)


def _emit_function_graph(stream: NDJSONStream, fn: Function, em: ExampleMappingResult) -> None:
    """Function 노드 + EM 노드/엣지 → NDJSON node/relationship 이벤트.

    프론트가 그래프를 함수 단위로 누적 렌더하도록 — 한 함수의 모든 변경을 묶어 emit.
    """
    stream.emit({
        "type": "node_event", "op": "merge", "label": "Function",
        "id": fn.function_id,
        "props": {
            "summary": fn.summary or "",
            "stereotype": fn.stereotype or "",
            "logical_name": fn.logical_name or "",
        },
    })
    for r in em.rules:
        stream.emit({
            "type": "node_event", "op": "merge", "label": "Rule",
            "id": r.rule_hash, "props": {"statement": r.statement},
        })
    for ex in em.examples:
        stream.emit({
            "type": "node_event", "op": "merge", "label": "Example",
            "id": ex.example_id,
            "props": {"description": ex.description, "is_boundary": ex.is_boundary},
        })
    for q in em.questions:
        stream.emit({
            "type": "node_event", "op": "merge", "label": "Question",
            "id": q.question_id, "props": {"text": q.text, "reason": q.reason},
        })

    for hr in em.has_rule:
        stream.emit({
            "type": "relationship_event", "op": "merge", "rel_type": "HAS_RULE",
            "from": {"label": "Function", "id": hr.function_id},
            "to":   {"label": "Rule",     "id": hr.rule_hash},
            "props": {"local_rule_id": hr.local_rule_id, "flow_id": hr.flow_id},
        })
    for he in em.has_example:
        stream.emit({
            "type": "relationship_event", "op": "merge", "rel_type": "HAS_EXAMPLE",
            "from": {"label": "Rule",    "id": he.rule_hash},
            "to":   {"label": "Example", "id": he.example_id},
        })
    for hq in em.has_question:
        stream.emit({
            "type": "relationship_event", "op": "merge", "rel_type": "HAS_QUESTION",
            "from": {"label": "Function", "id": hq.function_id},
            "to":   {"label": "Question", "id": hq.question_id},
        })
    for nx in em.next_edges:
        stream.emit({
            "type": "relationship_event", "op": "merge", "rel_type": "NEXT",
            "from": {"label": "Function", "id": nx.function_id, "rule_id": nx.from_rule_id},
            "to":   {"label": "Function", "id": nx.function_id, "rule_id": nx.to_rule_id},
        })
    for br in em.branch_edges:
        stream.emit({
            "type": "relationship_event", "op": "merge", "rel_type": "BRANCH",
            "from": {"label": "Function", "id": br.function_id, "rule_id": br.from_rule_id},
            "to":   {"label": "Function", "id": br.function_id, "rule_id": br.to_rule_id},
        })
    for at in em.affects_table:
        stream.emit({
            "type": "relationship_event", "op": "merge", "rel_type": "AFFECTS_TABLE",
            "from":  {"label": "Example", "id": at.example_id},
            "to":    {"label": "Table",   "name": at.table_name},
            "props": {"op": at.op},
        })
