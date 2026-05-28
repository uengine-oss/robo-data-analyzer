"""enrich_table_description_step — 테이블 description 통합 LLM 호출.

흐름:
1. ``step6_analyze_function`` 의 ``tableInsights`` (``Function.extra["table_insights"]``) 누적.
2. ``table_fqn`` 별로 candidates (모든 함수 관점의 role + columns) 모음.
3. ``prompts/<strategy>/table_description.yaml`` LLM 호출 → 통합 description.
4. Table/Column.analyzed_description Neo4j SET — DDL/카탈로그 1차 description 과 격리.

LLM 호출 = 테이블당 1 회 (병렬, ``max_concurrency``).
"""
from __future__ import annotations

import asyncio
import json
import time
from collections import defaultdict
from pathlib import Path
from typing import Optional

from langchain_core.runnables import RunnableConfig

from shared.workflow.step_utils import step_config

from shared.config.env_catalog import ENV
from shared.llm.cost_budget import BudgetExceededError
from shared.llm.fanout import gather_with_warmup
from data_models.function_analysis_output import TableDescription
from shared.llm.yaml_llm_runner import run_yaml_rule
from shared.log.logger import log_error, log_info, log_warning
from shared.neo4j.neo4j_client import Neo4jClient
from shared.neo4j.nodes.column import update_analyzed_descriptions as update_column_analyzed
from shared.neo4j.nodes.table import update_analyzed_descriptions as update_table_analyzed

_STEP = "step7.enrich_table_description"


async def enrich_table_description_step(state: dict, config: Optional[RunnableConfig] = None) -> dict:
    cfg = step_config(config)
    strategy = cfg.get("strategy")
    cost_budget = cfg.get("cost_budget")
    neo: Neo4jClient | None = cfg.get("neo_client")
    if strategy is None:
        log_warning(_STEP, "config_missing", "strategy 누락 — step skip")
        return {}

    functions = state.get("functions", [])
    if not functions:
        log_info(_STEP, "skip", reason="no functions")
        return {}
    started = time.monotonic()

    # table_fqn → [{function_summary, role, columns}, ...]
    table_candidates: dict[str, list[dict]] = defaultdict(list)
    # table_fqn → {column_name: description (가장 명확한 1개)}
    column_descriptions: dict[str, dict[str, str]] = defaultdict(dict)

    for fn in functions:
        for insight in fn.extra.get("table_insights", []) or []:
            table = insight.get("table") or ""
            if not table:
                continue
            table_candidates[table].append({
                "function_summary": fn.summary or fn.logical_name or fn.signature,
                "role": insight.get("role", ""),
                "columns": insight.get("columns", []) or [],
            })
            for col in insight.get("columns", []) or []:
                cname = col.get("name")
                cdesc = col.get("description")
                if cname and cdesc:
                    column_descriptions[table].setdefault(cname, cdesc)

    if not table_candidates:
        log_info(_STEP, "skip", reason="no table_insights aggregated")
        return {}

    log_info(
        _STEP, "start",
        tables=len(table_candidates),
        function_insights=sum(len(v) for v in table_candidates.values()),
        column_descriptions=sum(len(v) for v in column_descriptions.values()),
    )
    prompts_dir = Path(strategy.prompts_dir)
    semaphore = asyncio.Semaphore(ENV.pipeline.max_concurrency)

    async def _summarize_one(table_fqn: str, candidates: list[dict]):
        async with semaphore:
            try:
                return await run_yaml_rule(
                    prompts_dir=prompts_dir,
                    rule_name="table_description",
                    user_values={
                        "table_fqn": table_fqn,
                        "candidates_json": json.dumps(candidates, ensure_ascii=False, indent=2),
                    },
                    response_model=TableDescription,
                    budget=cost_budget,
                )
            except BudgetExceededError:
                raise            # 회로 차단기 즉시 전파
            except Exception as e:
                log_error(
                    "analysis_flow.step7_post_processing.enrich_table_description_step",
                    "table_description",
                    e,
                    table_fqn=table_fqn,
                )
                return None

    # 첫 테이블 1건으로 prefix 캐시 워밍 → 나머지 병렬 (cold 동시요청 캐시 미스 방지).
    results = await gather_with_warmup([
        _summarize_one(t, c) for t, c in table_candidates.items()
    ])

    # 분석 합성 설명 → analyzed_description 에 SET. description(DDL/카탈로그 1차설명)은
    # 안 건드린다 — AI 추론을 사실 칸에 섞지 않음. description_source 가 어느 칸을
    # 봐야 하는지 가리킨다 (DDL description 비었으면 'source').
    table_updates = [
        {"fqn": f"{r.db}.{r.schema_}.{r.name}", "analyzed_description": r.description}
        for r in results if r is not None
    ]
    if neo is not None and table_updates:
        await update_table_analyzed(neo, table_updates)

    column_updates = [
        {"fqn": f"{table_fqn}.{cname}", "analyzed_description": cdesc}
        for table_fqn, cols in column_descriptions.items()
        for cname, cdesc in cols.items()
    ]
    if neo is not None and column_updates:
        await update_column_analyzed(neo, column_updates)

    log_info(
        _STEP, "done",
        tables=len(table_candidates),
        descriptions=len(table_updates), columns=len(column_updates),
        elapsed_sec=f"{time.monotonic() - started:.2f}",
    )
    return {}
