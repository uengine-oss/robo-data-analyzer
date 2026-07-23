"""summarize_module_step — 모듈 / 클래스 / 패키지 종합 요약.

흐름:
1. CodeNode → Module 매칭 — ``file_path`` **+** ``start_line`` ⊂ Module range.
   (한 파일 안 여러 클래스 / 중첩 모듈 정확 처리)
2. 매칭 안 된 코드노드는 file-level Module (start_line=0, end_line=0) 로.
3. 각 Module 마다 멤버 코드노드들의 summary 종합 → ``module_summary.yaml`` LLM 호출.
4. Module.summary / stereotype Neo4j 업서트 (replace 로 label 등 보존).
"""
from __future__ import annotations

import time
from collections import defaultdict
from dataclasses import replace
from pathlib import Path
from typing import Optional

from langchain_core.runnables import RunnableConfig

from pipeline.execution.analysis_handoff import (
    POSTPROCESS_PRIORITY,
    AnalysisHandoffAborted,
    handoff_of,
)
from pipeline.execution.step_config import step_config
from pipeline.execution.pause_controller import PauseController

from graph.schema.graph_nodes import CodeNode, Module
from shared.config.settings import SETTINGS
from llm.usage.cost_budget import BudgetExceededError
from llm.fanout import gather_with_warmup, partition_llm_results
from pipeline.models.code_analysis_output import ModuleSummaryOutput
from llm.runners.yaml_llm_runner import run_yaml_rule
from shared.events.runtime_logger import log_error, log_info, log_warning
from shared.events.processing_events import step_processing_scope, track_processing
from graph.neo4j_client import Neo4jClient
from graph.nodes.module import upsert_modules
from pipeline.execution.graph_delta_stream import emit_nodes

_STEP = "step8.summarize_module"


async def summarize_module_step(state: dict, config: Optional[RunnableConfig] = None) -> dict:
    cfg = step_config(config)
    strategy = cfg.get("strategy")
    cost_budget = cfg.get("cost_budget")
    neo: Neo4jClient | None = cfg.get("neo_client")
    stream = cfg.get("stream")
    pause: PauseController = cfg.get("pause") or PauseController()
    # 이 step 이 읽는 것은 코드노드의 signature·summary·logical_name 뿐 — 전부 step7 의
    # #1 summary 에서 나온다(spec 096). 그래서 노드 전체 완료가 아니라 전 대상의 #1 확정을
    # 기다린다. 기다리는 동안 step7 이 슬롯을 쓰고, 열리는 즉시 남은 빈 슬롯을 채운다.
    # 결과는 어떤 경로로 끝나든 인계한다 — 안 하면 용어사전이 영원히 기다린다.
    handoff = handoff_of(cfg)
    try:
        result = await _summarize(state, handoff, strategy, cost_budget, neo, stream, pause)
    except AnalysisHandoffAborted:
        log_warning(_STEP, "analysis_aborted", "분석이 실패로 끝남 — 모듈 요약 skip")
        return {}
    except BaseException as error:
        # 실패한 요약을 인계하면 용어사전이 잘못된 문맥으로 392 콜을 쏜다. 원인을 넘긴다.
        handoff.abort(error)
        raise
    if not handoff.modules_published:
        # 요약을 못 낸 경로(설정 누락·모듈 0)도 반드시 인계 — 안 하면 용어사전이 굶는다.
        handoff.publish_modules(state.get("modules", []))
    return result


async def _summarize(
    state: dict, handoff, strategy, cost_budget, neo, stream, pause,
) -> dict:
    """모듈 요약 본체 — 게이트 통과 후. 인계·실패 정책은 호출부가 감싼다."""
    if strategy is None:
        log_warning(_STEP, "config_missing", "strategy 누락 — step skip")
        return {}

    await handoff.summaries()
    modules: list[Module] = state.get("modules", [])
    code_nodes: list[CodeNode] = handoff.merge(state.get("code_nodes", []))
    if not modules or not code_nodes:
        log_info(_STEP, "skip", modules=len(modules), code_nodes=len(code_nodes))
        return {}
    started = time.monotonic()
    log_info(_STEP, "start", modules=len(modules), code_nodes=len(code_nodes),
             concurrency=SETTINGS.analysis_pipeline.max_concurrency)

    # CodeNode → Module 매칭 (file_path + line range)
    fn_by_module = _match_code_to_modules(code_nodes, modules)

    prompts_dir = Path(strategy.prompts_dir)
    # run 공유 슬롯 — step7 과 같은 세마포어를 낮은 우선순위로 쓴다 (spec 096).
    slots = handoff.slots

    async def _summarize_one(module: Module) -> Module:
        """성공 시 새 module / 실패 시 raise (BudgetExceededError 또는 LLM 예외).

        fail 정책 처리 (log + 원본 반환) 는 메인 루틴이 책임 — _summarize_one 은
        성공 경로만 책임 (단일 책임).
        """
        members = fn_by_module.get(module.id, [])
        if not members:
            return module
        member_text = "\n".join(
            f"- {fn.signature}: {fn.summary or fn.logical_name or '(분석 전)'}"
            for fn in members
        )
        async with track_processing("Module", module.id):
            async with slots.at(POSTPROCESS_PRIORITY):
                result: ModuleSummaryOutput = await run_yaml_rule(
                    prompts_dir=prompts_dir,
                    rule_name="module_summary",
                    user_values={
                        "id": module.id,
                        "name": module.name,
                        "member_code_nodes": member_text,
                    },
                    response_model=ModuleSummaryOutput,
                    budget=cost_budget,
                )
        # replace — label / comment / annotations 등 원본 필드 보존, 분석분만 갱신.
        # logical_name 은 여기서 채움 — 헤더 임시 정보가 아니라 멤버 분석 종합에서 도출.
        return replace(
            module,
            summary=result.summary,
            stereotype=result.stereotype,
            logical_name=result.logical_name or None,
        )

    # 첫 모듈 1건으로 prefix 캐시 워밍 → 나머지 병렬. fail 정책 = 개별 모듈 LLM 실패 시
    # 로그 + 원본 module 반환 (분석 흐름 유지). BudgetExceededError 만 즉시 전파.
    async def _run_fanout() -> list:
        return await gather_with_warmup(
            [_summarize_one(m) for m in modules], return_exceptions=True,
        )

    with step_processing_scope(_STEP, stream):
        results = await pause.run_restartable_during_pause(_run_fanout)
    updated = _apply_fail_policy(modules, results)
    # 용어사전이 모듈 요약을 문맥으로 읽는다 — Neo4j 적재를 기다리지 않고 바로 인계한다.
    handoff.publish_modules(updated)

    if neo is not None:
        await upsert_modules(neo, updated)
    # 모듈 stereotype(FILE→Service 등)을 라이브 발신 — labels[1:]에 역할 실어 프론트 즉시 반영(spec 034).
    await emit_nodes(stream, updated)
    summarized = sum(1 for m in updated if getattr(m, "summary", None))
    log_info(
        _STEP, "done",
        modules=len(modules), summarized=summarized,
        elapsed_sec=f"{time.monotonic() - started:.2f}",
    )
    return {"modules": updated}


def _apply_fail_policy(modules: list[Module], results: list) -> list[Module]:
    """fan-out 결과에 fail 정책 적용 — 예외면 원본 module + log, BudgetExceededError 는 raise.

    분류 규칙 자체는 ``partition_llm_results`` 단일 정의처가 갖는다 (spec 046 FR-011).
    여기 정책 = ``on_failure="keep"`` — 요약 실패한 모듈은 **원본 그대로** 유지해 분석 흐름을
    끊지 않는다 (모듈이 리스트에서 사라지면 후속 step·artifact 가 노드를 잃는다).
    """
    updated, _failed = partition_llm_results(
        modules, results,
        component="pipeline.steps.step8_post_processing.summarize_module_step",
        operation="module_summary",
        id_of=lambda m: m.id,
        on_failure="keep",
    )
    return updated


def _match_code_to_modules(
    code_nodes: list[CodeNode],
    modules: list[Module],
) -> dict[str, list[CodeNode]]:
    """CodeNode → Module 매칭 (file_path + 라인 범위).

    한 파일 안 여러 module (e.g. Java inner class) → 가장 좁은 범위 매칭.
    file-level module (range 없음) 은 fallback.
    """
    # file_path → [(start, end, module_id)] sorted by 범위 좁은 순
    by_file: dict[str, list[tuple[int, int, str]]] = defaultdict(list)
    for m in modules:
        by_file[m.file_path].append((m.start_line, m.end_line, m.id))
    for k in by_file:
        # 범위 좁은 모듈 먼저 — function 매칭 시 inner class 우선
        by_file[k].sort(key=lambda x: (x[1] - x[0]) if x[1] > 0 else 10**9)

    result: dict[str, list[CodeNode]] = defaultdict(list)
    for fn in code_nodes:
        candidates = by_file.get(fn.file_path, [])
        if not candidates:
            continue
        matched_id = None
        for start, end, mid in candidates:
            if start == 0 and end == 0:
                # file-level — 다른 후보 없으면 사용
                if matched_id is None:
                    matched_id = mid
                continue
            if start <= fn.start_line <= end:
                matched_id = mid
                break
        if matched_id is not None:
            result[matched_id].append(fn)
    return result
