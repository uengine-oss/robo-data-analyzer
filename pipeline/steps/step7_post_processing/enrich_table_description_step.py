"""enrich_table_description_step — 테이블 description 통합 LLM 호출.

흐름:
1. ``step6_analyze_code`` 의 ``tableInsights`` (``CodeNode.extra["table_insights"]``) 누적.
2. ``table_fqn`` 별로 candidates (모든 코드노드 관점의 role + columns) 모음.
3. ``llm/prompts/<strategy>/table_description.yaml`` LLM 호출 → 통합 description.
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

from pipeline.execution.step_config import step_config

from system.env_catalog import ENV
from llm.client.usage.cost_budget import BudgetExceededError
from llm.client.llm_fanout import gather_with_warmup, partition_llm_results
from pipeline.data_models.code_analysis_output import TableDescription
from llm.client.runners.yaml_llm_runner import run_yaml_rule
from shared.log.logger import log_error, log_info, log_warning
from shared.log.processing_progress import step_processing_scope, track_processing
from graph.neo4j_client import Neo4jClient
from graph.nodes.column import update_analyzed_descriptions as update_column_analyzed
from graph.nodes.table import update_analyzed_descriptions as update_table_analyzed
from pipeline.steps.step7_post_processing.table_logical_names import build_column_updates

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

    code_nodes = state.get("code_nodes", [])
    if not code_nodes:
        log_info(_STEP, "skip", reason="no code_nodes")
        return {}
    started = time.monotonic()

    # table_fqn → [{code_summary, role, columns}, ...]
    table_candidates: dict[str, list[dict]] = defaultdict(list)
    # table_fqn → {column_name: description (가장 명확한 1개)}
    column_descriptions: dict[str, dict[str, str]] = defaultdict(dict)
    # table_fqn → {column_name: logical_name (가장 명확한 1개)} — 컬럼 논리명(spec 028)
    column_logicals: dict[str, dict[str, str]] = defaultdict(dict)

    for fn in code_nodes:
        for insight in fn.extra.get("table_insights", []) or []:
            table = insight.get("table") or ""
            if not table:
                continue
            table_candidates[table].append({
                "code_summary": fn.summary or fn.logical_name or fn.signature,
                "role": insight.get("role", ""),
                "columns": insight.get("columns", []) or [],
            })
            for col in insight.get("columns", []) or []:
                cname = col.get("name")
                cdesc = col.get("description")
                if cname and cdesc:
                    column_descriptions[table].setdefault(cname, cdesc)
                clog = col.get("logical_name")
                if cname and clog:
                    column_logicals[table].setdefault(cname, clog)

    if not table_candidates:
        log_info(_STEP, "skip", reason="no table_insights aggregated")
        return {}

    log_info(
        _STEP, "start",
        tables=len(table_candidates),
        code_insights=sum(len(v) for v in table_candidates.values()),
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

    # 분석 합성 설명/논리명 → 노드에 SET. ★id = 원본 table_fqn(= 노드 id, 결정론) 직접 사용.
    # LLM 이 반환한 db/schema/name 으로 id 를 재조립하면 오파싱(name='TB' 등)으로 불일치·유실
    # (컬럼 경로와 동일하게 원본 fqn 사용 — spec 024). r 은 설명·논리명 내용만 쓴다.
    table_updates = [
        {"id": table_fqn, "logical_name": r.logical_name, "analyzed_description": r.description}
        for table_fqn, r in zip(table_fqns, results) if r is not None
    ]
    if neo is not None and table_updates:
        matched = await update_table_analyzed(neo, table_updates)
        _warn_unmatched("테이블", table_updates, matched)

    # 컬럼 논리명(spec 028)도 동반 SET — 빈값은 column.py SET_ANALYZED 가 미저장 처리.
    # description 없이 logical_name만 반환된 컬럼도 보존한다. 종전에는
    # column_descriptions를 반복해 이 경우 logical_name이 집계 단계에서 소실됐다.
    column_updates = build_column_updates(
        table_candidates, column_descriptions, column_logicals,
    )
    if neo is not None and column_updates:
        matched = await update_column_analyzed(neo, column_updates)
        _warn_unmatched("컬럼", column_updates, matched)

    # ★ logical_name 도 함께 반영 (FR-013) — Neo4j 에만 넣고 state 를 안 고치면
    #   artifact(ast.json)엔 빈값이 남아 그래프와 다른 사실을 말한다.
    updated_tables = _replace_by_id(
        state.get("tables", []), table_updates, "analyzed_description", "logical_name",
    )
    updated_columns = _replace_by_id(
        state.get("columns", []), column_updates, "analyzed_description", "logical_name",
    )

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

    분류 규칙 자체는 ``partition_llm_results`` 단일 정의처가 갖는다 (spec 046 FR-011).
    여기 정책 = ``on_failure="none"`` — 실패한 테이블은 **자리(None)를 남긴다**.
    호출부가 ``zip(table_fqns, results)`` 로 짝지으므로 길이가 맞아야 한다.
    """
    out, _failed = partition_llm_results(
        table_fqns, results,
        component="pipeline.steps.step7_post_processing.enrich_table_description_step",
        operation="table_description",
        id_of=lambda fqn: fqn,
        on_failure="none",
    )
    return out


def _warn_unmatched(kind: str, updates: list[dict], matched: int) -> None:
    """미반영 = LLM 설명의 id 가 실제 노드와 불일치(= 노드 아닌 대상) → 그 설명만 버려진다.
    실제 노드는 id 일치 시 채워지므로 이건 **컬럼 유실이 아니라 무해한 잉여**다. 따라서
    error 로 오해되지 않게 INFO(analyzer.log)에만 남긴다 — error.log(WARNING+) 에 안 띄움.
    (진짜 유실 = '쓰는 컬럼이 빈값'은 spec 027 컬럼 주입으로 예방. spec 024→027 정정.)
    """
    if matched < len(updates):
        log_info(
            _STEP, "analyzed_desc_unmatched",
            f"{kind} 분석설명 {len(updates) - matched}/{len(updates)}건 미반영"
            " (노드 아닌 대상 = 무해한 잉여, 컬럼 유실 아님)",
        )


def _replace_by_id(nodes: list, updates: list[dict], *attrs: str) -> list:
    """``updates`` 의 id → ``attrs`` 값들을 같은 노드에 박은 새 인스턴스 리스트.

    매치 안 된 노드는 그대로 유지 — state 키엔 reducer 가 없어(default replace) 전체
    리스트를 돌려줘야 다음 step·artifact 가 노드를 잃지 않는다.

    **여러 필드**를 받는다 (spec 045 FR-013): 옛 단일 필드 버전은 ``analyzed_description``
    만 되돌려 써서 ``logical_name`` 이 Neo4j 에만 들어가고 state/artifact 에는 빈값으로 남았다
    → 그래프와 artifact 가 다른 사실을 말하는 검증 사각지대. 빈값은 덮어쓰지 않는다
    (LLM 이 논리명을 못 낸 테이블의 기존 값을 지우지 않도록).
    """
    id_to_vals = {u["id"]: u for u in updates}
    return [
        replace(n, **{
            a: id_to_vals[n.id][a]
            for a in attrs
            if a in id_to_vals[n.id] and id_to_vals[n.id][a]
        })
        if n.id in id_to_vals else n
        for n in nodes
    ]
