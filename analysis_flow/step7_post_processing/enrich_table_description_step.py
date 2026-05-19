"""enrich_table_description_step — 테이블 description 통합 LLM 호출.

흐름:
1. ``step6_analyze_function`` 의 ``tableInsights`` (``Function.extra["table_insights"]``) 누적.
2. ``table_fqn`` 별로 candidates (모든 함수 관점의 role + columns) 모음.
3. ``prompts/<strategy>/table_description.yaml`` LLM 호출 → 통합 description.
4. Table.description Neo4j SET + Column.description 도 같이 갱신.

LLM 호출 = 테이블당 1 회 (병렬, ``max_concurrency``).
"""
from __future__ import annotations

import asyncio
import json
from collections import defaultdict
from pathlib import Path
from typing import Optional

from langchain_core.runnables import RunnableConfig

from data_models.graph_nodes import Column
from shared.config.env_catalog import ENV
from shared.llm.cost_budget import BudgetExceededError
from shared.llm.llm_output_models import TableDescription
from shared.llm.middleware.call_limit import ModelCallLimitExceeded
from shared.llm.yaml_llm_runner import run_yaml_rule
from shared.logging_and_stream.logger import log_error
from shared.neo4j.neo4j_client import Neo4jClient
from shared.neo4j.nodes.column import upsert_columns
from shared.neo4j.nodes.table import update_descriptions


async def enrich_table_description_step(state: dict, config: Optional[RunnableConfig] = None) -> dict:
    from shared.logging_and_stream.logger import log_info, log_warning  # local import — 충돌 회피
    import time as _time

    _STEP = "step7.enrich_table_description"
    cfg = (config or {}).get("configurable", {}) if isinstance(config, dict) else {}
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
    started = _time.monotonic()

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
            except (BudgetExceededError, ModelCallLimitExceeded):
                raise            # 회로 차단기 즉시 전파
            except Exception as e:
                log_error(
                    "analysis_flow.step7_post_processing.enrich_table_description_step",
                    "table_description",
                    e,
                    table_fqn=table_fqn,
                )
                return None

    results = await asyncio.gather(*[
        _summarize_one(t, c) for t, c in table_candidates.items()
    ])

    # Table.description 업서트 — fqn 단일 식별
    table_updates: list[dict] = []
    for r in results:
        if r is None:
            continue
        table_updates.append({
            "fqn": f"{r.db}.{r.schema_}.{r.name}",
            "description": r.description,
        })

    if neo is not None and table_updates:
        await update_descriptions(neo, table_updates)

    # Column.description 도 함께 갱신 (tableInsights 의 column 의미)
    column_upserts: list[Column] = []
    for table_fqn, cols in column_descriptions.items():
        for cname, cdesc in cols.items():
            column_upserts.append(Column(
                fqn=f"{table_fqn}.{cname}",
                name=cname,
                dtype="",
                description=cdesc,
            ))
    if neo is not None and column_upserts:
        await upsert_columns(neo, column_upserts)

    log_info(
        _STEP, "done",
        tables=len(table_candidates),
        descriptions=len(table_updates), columns=len(column_upserts),
        elapsed_sec=f"{_time.monotonic() - started:.2f}",
    )
    return {}
