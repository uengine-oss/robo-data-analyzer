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
from dataclasses import replace
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
from shared.log.processing_progress import step_processing_scope, track_processing
from shared.neo4j.neo4j_client import Neo4jClient
from shared.neo4j.nodes.column import update_analyzed_descriptions as update_column_analyzed
from shared.neo4j.nodes.table import update_analyzed_descriptions as update_table_analyzed

_STEP = "step7.enrich_table_description"


async def enrich_table_description_step(state: dict, config: Optional[RunnableConfig] = None) -> dict:
    cfg = step_config(config)
    strategy = cfg.get("strategy")
    cost_budget = cfg.get("cost_budget")
    neo: Neo4jClient | None = cfg.get("neo_client")
    stream = cfg.get("stream")
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
    # 병렬 그룹 공유 세마포어 (executor 주입) — 없으면 단독 생성(순차 실행 시 동일).
    semaphore = cfg.get("llm_semaphore") or asyncio.Semaphore(ENV.pipeline.max_concurrency)

    async def _summarize_one(table_fqn: str, candidates: list[dict]) -> TableDescription:
        """성공 시 TableDescription / 실패 시 raise. fail 정책은 메인이 책임 (단일 책임)."""
        async with track_processing("Table", table_fqn):
            async with semaphore:
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

    # 첫 테이블 1건으로 prefix 캐시 워밍 → 나머지 병렬. fail 정책 = 개별 테이블 LLM 실패 시
    # 로그 + 결과 drop (분석 흐름 유지). BudgetExceededError 만 즉시 전파.
    table_fqns = list(table_candidates.keys())
    with step_processing_scope(_STEP, stream):
        raw_results = await gather_with_warmup(
            [_summarize_one(t, table_candidates[t]) for t in table_fqns],
            return_exceptions=True,
        )
    results = _apply_table_fail_policy(table_fqns, raw_results)

    # 분석 합성 설명 → analyzed_description. description (DDL/catalog 1차설명) 은 안 건드림.
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

    updated_tables = _replace_by_fqn(state.get("tables", []), table_updates, "analyzed_description")
    updated_columns = _replace_by_fqn(state.get("columns", []), column_updates, "analyzed_description")

    log_info(
        _STEP, "done",
        tables=len(table_candidates),
        descriptions=len(table_updates), columns=len(column_updates),
        elapsed_sec=f"{time.monotonic() - started:.2f}",
    )
    return {"tables": updated_tables, "columns": updated_columns}


def _apply_table_fail_policy(
    table_fqns: list[str], results: list,
) -> list[Optional[TableDescription]]:
    """fan-out 결과에 fail 정책 적용 — 예외면 None drop + log, BudgetExceededError 는 raise.

    단일 책임 — _summarize_one = 성공만 / 여기 = fail 정책 (응집 ★ DRY).
    """
    out: list[Optional[TableDescription]] = []
    for table_fqn, result in zip(table_fqns, results):
        if isinstance(result, BudgetExceededError):
            raise result        # 회로 차단기는 상위로 전파
        if isinstance(result, BaseException):
            log_error(
                "analysis_flow.step7_post_processing.enrich_table_description_step",
                "table_description",
                result,
                table_fqn=table_fqn,
            )
            out.append(None)
        else:
            out.append(result)
    return out


def _replace_by_fqn(nodes: list, updates: list[dict], attr: str) -> list:
    """``updates`` 의 fqn → ``attr`` 값을 같은 노드에 박은 새 인스턴스 리스트.

    매치 안 된 노드는 그대로 유지 — state 키엔 reducer 가 없어(default replace) 전체
    리스트를 돌려줘야 다음 step·artifact 가 노드를 잃지 않는다.
    """
    fqn_to_val = {u["fqn"]: u[attr] for u in updates}
    return [
        replace(n, **{attr: fqn_to_val[n.fqn]}) if n.fqn in fqn_to_val else n
        for n in nodes
    ]
