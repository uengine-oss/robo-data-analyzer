"""summarize_module_step — 모듈 / 클래스 / 패키지 종합 요약.

흐름:
1. CodeNode → Module 매칭 — ``file_path`` **+** ``start_line`` ⊂ Module range.
   (한 파일 안 여러 클래스 / 중첩 모듈 정확 처리)
2. 매칭 안 된 코드노드는 file-level Module (start_line=0, end_line=0) 로.
3. 각 Module 마다 멤버 코드노드들의 summary 종합 → ``module_summary.yaml`` LLM 호출.
4. Module.summary / stereotype Neo4j 업서트 (replace 로 label 등 보존).
"""
from __future__ import annotations

import asyncio
import time
from collections import defaultdict
from dataclasses import replace
from pathlib import Path
from typing import Optional

from langchain_core.runnables import RunnableConfig

from shared.workflow.step_utils import step_config

from data_models.graph_nodes import CodeNode, Module
from shared.config.env_catalog import ENV
from shared.llm.cost_budget import BudgetExceededError
from shared.llm.fanout import gather_with_warmup
from data_models.code_analysis_output import ModuleSummaryOutput
from shared.llm.yaml_llm_runner import run_yaml_rule
from shared.log.logger import log_error, log_info, log_warning
from shared.log.processing_progress import step_processing_scope, track_processing
from shared.neo4j.neo4j_client import Neo4jClient
from shared.neo4j.nodes.module import upsert_modules
from shared.workflow.graph_stream import emit_nodes

_STEP = "step7.summarize_module"


async def summarize_module_step(state: dict, config: Optional[RunnableConfig] = None) -> dict:
    cfg = step_config(config)
    strategy = cfg.get("strategy")
    cost_budget = cfg.get("cost_budget")
    neo: Neo4jClient | None = cfg.get("neo_client")
    stream = cfg.get("stream")
    if strategy is None:
        log_warning(_STEP, "config_missing", "strategy 누락 — step skip")
        return {}

    modules: list[Module] = state.get("modules", [])
    code_nodes: list[CodeNode] = state.get("code_nodes", [])
    if not modules or not code_nodes:
        log_info(_STEP, "skip", modules=len(modules), code_nodes=len(code_nodes))
        return {}
    started = time.monotonic()
    log_info(_STEP, "start", modules=len(modules), code_nodes=len(code_nodes),
             concurrency=ENV.pipeline.max_concurrency)

    # CodeNode → Module 매칭 (file_path + line range)
    fn_by_module = _match_code_to_modules(code_nodes, modules)

    prompts_dir = Path(strategy.prompts_dir)
    # 병렬 그룹 공유 세마포어 (executor 주입) — 없으면 단독 생성(순차 실행 시 동일).
    semaphore = cfg.get("llm_semaphore") or asyncio.Semaphore(ENV.pipeline.max_concurrency)

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
            async with semaphore:
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
    with step_processing_scope(_STEP, stream):
        results = await gather_with_warmup(
            [_summarize_one(m) for m in modules], return_exceptions=True,
        )
    updated = _apply_fail_policy(modules, results)

    if neo is not None:
        await upsert_modules(neo, updated)
    # 모듈 stereotype(FILE→Service 등)을 라이브 발신 — labels[1:]에 역할 실어 프론트 즉시 반영(spec 034).
    emit_nodes(stream, updated)
    summarized = sum(1 for m in updated if getattr(m, "summary", None))
    log_info(
        _STEP, "done",
        modules=len(modules), summarized=summarized,
        elapsed_sec=f"{time.monotonic() - started:.2f}",
    )
    return {"modules": updated}


def _apply_fail_policy(modules: list[Module], results: list) -> list[Module]:
    """fan-out 결과에 fail 정책 적용 — 예외면 원본 module + log, BudgetExceededError 는 raise.

    ``_summarize_one`` 이 raise 한 예외를 여기서 받아 분석 흐름 유지 정책 (원본 반환)
    실행. 단일 책임 — _summarize_one = 성공만 / 여기 = fail 정책.
    """
    updated: list[Module] = []
    for module, result in zip(modules, results):
        if isinstance(result, BudgetExceededError):
            raise result        # 회로 차단기는 상위로 전파
        if isinstance(result, BaseException):
            log_error(
                "analysis_flow.step7_post_processing.summarize_module_step",
                "module_summary",
                result,
                module_id=module.id,
            )
            updated.append(module)
        else:
            updated.append(result)
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
