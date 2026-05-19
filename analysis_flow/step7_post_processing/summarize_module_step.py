"""summarize_module_step — 모듈 / 클래스 / 패키지 종합 요약.

흐름:
1. Function → Module 매칭 — ``file_path`` **+** ``start_line`` ⊂ Module range.
   (한 파일 안 여러 클래스 / 중첩 모듈 정확 처리)
2. 매칭 안 된 함수는 file-level Module (start_line=0, end_line=0) 로.
3. 각 Module 마다 멤버 함수들의 summary 종합 → ``module_summary.yaml`` LLM 호출.
4. Module.summary / module_stereotype Neo4j 업서트.
"""
from __future__ import annotations

import asyncio
import time
from collections import defaultdict
from pathlib import Path
from typing import Optional

from langchain_core.runnables import RunnableConfig

from data_models.graph_nodes import Function, Module
from shared.config.env_catalog import ENV
from shared.llm.cost_budget import BudgetExceededError
from shared.llm.llm_output_models import ModuleSummaryOutput
from shared.llm.middleware.call_limit import ModelCallLimitExceeded
from shared.llm.yaml_llm_runner import run_yaml_rule
from shared.logging_and_stream.logger import log_error, log_info, log_warning
from shared.neo4j.neo4j_client import Neo4jClient
from shared.neo4j.nodes.module import upsert_modules

_STEP = "step7.summarize_module"


async def summarize_module_step(state: dict, config: Optional[RunnableConfig] = None) -> dict:
    cfg = (config or {}).get("configurable", {}) if isinstance(config, dict) else {}
    strategy = cfg.get("strategy")
    cost_budget = cfg.get("cost_budget")
    neo: Neo4jClient | None = cfg.get("neo_client")
    if strategy is None:
        log_warning(_STEP, "config_missing", "strategy 누락 — step skip")
        return {}

    modules: list[Module] = state.get("modules", [])
    functions: list[Function] = state.get("functions", [])
    if not modules or not functions:
        log_info(_STEP, "skip", modules=len(modules), functions=len(functions))
        return {}
    started = time.monotonic()
    log_info(_STEP, "start", modules=len(modules), functions=len(functions),
             concurrency=ENV.pipeline.max_concurrency)

    # Function → Module 매칭 (file_path + line range)
    fn_by_module = _match_functions_to_modules(functions, modules)

    prompts_dir = Path(strategy.prompts_dir)
    semaphore = asyncio.Semaphore(ENV.pipeline.max_concurrency)

    async def _summarize_one(module: Module):
        members = fn_by_module.get(module.module_id, [])
        if not members:
            return module
        member_text = "\n".join(
            f"- {fn.signature}: {fn.summary or fn.logical_name or '(분석 전)'}"
            for fn in members
        )
        async with semaphore:
            try:
                result: ModuleSummaryOutput = await run_yaml_rule(
                    prompts_dir=prompts_dir,
                    rule_name="module_summary",
                    user_values={
                        "module_id": module.module_id,
                        "name": module.name,
                        "member_functions": member_text,
                    },
                    response_model=ModuleSummaryOutput,
                    budget=cost_budget,
                )
            except (BudgetExceededError, ModelCallLimitExceeded):
                raise            # 회로 차단기는 상위로 전파
            except Exception as e:
                # 개별 모듈 요약 실패 → 로깅 후 원본 module 반환 (요약 없이).
                log_error(
                    "analysis_flow.step7_post_processing.summarize_module_step",
                    "module_summary",
                    e,
                    module_id=module.module_id,
                )
                return module
        return Module(
            module_id=module.module_id,
            name=module.name,
            file_path=module.file_path,
            start_line=module.start_line,
            end_line=module.end_line,
            summary=result.summary,
            module_stereotype=result.module_stereotype,
        )

    updated = await asyncio.gather(*[_summarize_one(m) for m in modules])

    if neo is not None:
        await upsert_modules(neo, updated)
    summarized = sum(1 for m in updated if getattr(m, "summary", None))
    log_info(
        _STEP, "done",
        modules=len(modules), summarized=summarized,
        elapsed_sec=f"{time.monotonic() - started:.2f}",
    )
    return {"modules": list(updated)}


def _match_functions_to_modules(
    functions: list[Function],
    modules: list[Module],
) -> dict[str, list[Function]]:
    """Function → Module 매칭 (file_path + 라인 범위).

    한 파일 안 여러 module (e.g. Java inner class) → 가장 좁은 범위 매칭.
    file-level module (range 없음) 은 fallback.
    """
    # file_path → [(start, end, module_id)] sorted by 범위 좁은 순
    by_file: dict[str, list[tuple[int, int, str]]] = defaultdict(list)
    for m in modules:
        by_file[m.file_path].append((m.start_line, m.end_line, m.module_id))
    for k in by_file:
        # 범위 좁은 모듈 먼저 — function 매칭 시 inner class 우선
        by_file[k].sort(key=lambda x: (x[1] - x[0]) if x[1] > 0 else 10**9)

    result: dict[str, list[Function]] = defaultdict(list)
    for fn in functions:
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
