"""build_components_step — 함수 호출 군집을 FUNCTION 노드 속성으로 부여.

robo 가 이미 만든 호출그래프(state 의 `functions`/`calls_edges`)만으로:
1. **나누기(결정론, LLM 0):** 내부 호출 그래프에 Leiden 군집화 → 함수들을 군집(부품)으로.
2. **이름(LLM, 군집당 1콜·전부 병렬):** 멤버 함수의 `[핵심]` 한 줄만 모아 →
   `run_yaml_rule("component_naming")` 로 사람이 읽을 이름 생성.
3. **적재:** 각 멤버 FUNCTION 노드에 `component_id`·`component_name` 속성을 SET
   (health_metrics 와 동일한 배치 UNWIND SET 패턴).

★ 그룹을 합성 노드(`:COMPONENT`)로 materialize 하지 않는다 — 멤버십은 **`FUNCTION.component_id`
  속성이 단일 진실**이고, 이름·그룹간 관계는 전부 거기서 파생한다:
    - 시각화(바운디드 컨텍스트 영역)는 `component_id` 로 뷰에서 hull 오버레이.
    - 그룹↔그룹 호출은 저장하지 않고 `CALLS` 엣지 + `component_id` 로 온디맨드 GROUP BY.
  → 그래프에 합성 노드/엣지 0 → 도달성·중심성·임팩트 분석 왜곡 없음.
  Leiden(leidenalg/igraph)은 군집화 시점에 lazy import (미설치면 명시 에러).
"""
from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Optional

from langchain_core.runnables import RunnableConfig

from data_models.function_analysis_output import ComponentNamingOutput
from data_models.graph_edges import Calls
from data_models.graph_nodes import Function
from shared.config.env_catalog import ENV
from shared.llm.cost_budget import BudgetExceededError
from shared.llm.fanout import gather_with_warmup
from shared.llm.yaml_llm_runner import run_yaml_rule
from shared.log.logger import log_error, log_info, log_warning
from shared.log.processing_progress import step_processing_scope, track_processing
from shared.neo4j.neo4j_client import Neo4jClient
from shared.workflow.step_utils import step_config


_STEP = "step7.build_components"

# 군집/이름 파라미터 (튜닝 가능).
MIN_COMPONENT_SIZE = 2     # 멤버 1개(고립 함수)는 군집으로 X (노이즈 차단)
NAME_MAX_MEMBERS = 40      # 이름 입력에 넣을 멤버 [핵심] 줄 상한 (토큰 안전)
LEIDEN_SEED = 42           # 결정론 — 같은 그래프 → 같은 군집

# 멤버 FUNCTION 노드에 군집 멤버십을 속성으로 SET (합성 노드 없음). health_metrics 와 동일 패턴.
_SET_QUERY = """
UNWIND $items AS item
MATCH (node {id: item.id})
SET node.component_id = item.component_id,
    node.component_name = item.component_name
"""


async def build_components_step(state: dict, config: Optional[RunnableConfig] = None) -> dict:
    """호출그래프 → Leiden 군집 → 병렬 이름 → FUNCTION 노드에 component_id/name SET."""
    cfg = step_config(config)
    strategy = cfg.get("strategy")
    cost_budget = cfg.get("cost_budget")
    neo: Neo4jClient | None = cfg.get("neo_client")
    stream = cfg.get("stream")
    if strategy is None:
        log_warning(_STEP, "config_missing", "strategy 누락 — step skip")
        return {}

    functions: list[Function] = state.get("functions", [])
    calls: list[Calls] = state.get("calls_edges", [])
    if not functions:
        log_info(_STEP, "skip", reason="functions 0")
        return {}

    started = time.monotonic()
    # 1. 나누기 (결정론)
    clusters = _cluster(functions, calls)         # [(component_id, [member_function_id...])]
    if not clusters:
        log_info(_STEP, "skip", reason="군집 없음 (내부 호출 부족)")
        return {}
    log_info(_STEP, "start", functions=len(functions), components=len(clusters),
             concurrency=ENV.pipeline.max_concurrency)

    # 2. 이름 (LLM, 병렬) — 군집당 사람이 읽을 이름 하나.
    prompts_dir = Path(strategy.prompts_dir)
    semaphore = cfg.get("llm_semaphore") or asyncio.Semaphore(ENV.pipeline.max_concurrency)
    fn_by_id = {f.id: f for f in functions}

    async def _name_one(component_id: str, members: list[str]) -> str:
        member_fns = [fn_by_id[m] for m in members if m in fn_by_id]
        lines = [f"- {_headline(fn)}" for fn in member_fns[:NAME_MAX_MEMBERS]]
        extra = len(member_fns) - len(lines)
        if extra > 0:
            lines.append(f"- (외 {extra}개 함수)")
        members_text = "\n".join(lines)
        async with track_processing("Component", component_id):
            async with semaphore:
                result: ComponentNamingOutput = await run_yaml_rule(
                    prompts_dir=prompts_dir,
                    rule_name="component_naming",
                    user_values={"component_id": component_id, "members": members_text},
                    response_model=ComponentNamingOutput,
                    budget=cost_budget,
                )
        return result.name

    with step_processing_scope(_STEP, stream):
        results = await gather_with_warmup(
            [_name_one(cid, members) for cid, members in clusters], return_exceptions=True,
        )
    names = _resolve_names(clusters, results, fn_by_id)   # component_id → name

    # 3. 적재 — 각 멤버 FUNCTION 노드에 component_id/name 속성 SET (합성 노드/엣지 없음).
    items = [
        {"id": m, "component_id": cid, "component_name": names[cid]}
        for cid, members in clusters
        for m in members
    ]
    if neo is not None and items:
        await neo.run_batch_unwind(_SET_QUERY, items)
    elif neo is None:
        log_warning(_STEP, "config_missing", "neo_client 누락 — component 속성 SET skip (계산만)")

    log_info(
        _STEP, "done",
        components=len(clusters), members_tagged=len(items),
        elapsed_sec=f"{time.monotonic() - started:.2f}",
    )
    # 합성 노드/엣지를 state 에 남기지 않는다 — 멤버십은 FUNCTION.component_id 가 단일 진실.
    return {}


def _cluster(functions: list[Function], calls: list[Calls]) -> list[tuple[str, list[str]]]:
    """내부 호출 그래프에 Leiden → [(component_id, [member_id...])]. 결정론(seed 고정).

    Leiden(leidenalg/igraph)은 여기서 lazy import — 미설치면 명시 에러(설치 안내).
    component_id = 멤버 정렬 첫 function_id 기반 → 같은 그래프 재분석 시 안정 키.
    """
    ids = [f.id for f in functions]
    id_set = set(ids)
    edges = [
        (c.caller_id, c.callee_id)
        for c in calls
        if c.scope == "internal"
        and c.caller_id in id_set
        and c.callee_id in id_set
        and c.caller_id != c.callee_id
    ]
    if not edges:
        return []

    try:
        import igraph as ig
        import leidenalg
    except ImportError as e:  # noqa: BLE001 — 명시 안내 후 전파
        raise RuntimeError(
            "leidenalg/igraph 미설치 — `pip install leidenalg igraph` 필요 "
            "(requirements.txt 반영됨)"
        ) from e

    g = ig.Graph(directed=False)
    g.add_vertices(ids)                 # 정점 이름 = function_id (index i ↔ ids[i])
    g.add_edges(edges)                  # 이름 기반 엣지 (중복은 무방 — 가중 동등)

    partition = leidenalg.find_partition(
        g, leidenalg.ModularityVertexPartition, seed=LEIDEN_SEED,
    )

    out: list[tuple[str, list[str]]] = []
    for community in partition:
        members = sorted(ids[i] for i in community)
        if len(members) < MIN_COMPONENT_SIZE:
            continue                    # 고립/초소 군집 = 노이즈 → 군집 X
        out.append((f"COMPONENT:{members[0]}", members))
    return out


def _resolve_names(
    clusters: list[tuple[str, list[str]]],
    results: list,
    fn_by_id: dict[str, Function],
) -> dict[str, str]:
    """component_id → 이름. LLM 실패 시 대표 함수 [핵심]로 fallback, BudgetExceededError 는 전파."""
    names: dict[str, str] = {}
    for (cid, members), result in zip(clusters, results):
        if isinstance(result, BudgetExceededError):
            raise result
        if isinstance(result, BaseException):
            log_error(
                "analysis_flow.step7_post_processing.build_components_step",
                "component_naming", result, component_id=cid,
            )
            first = fn_by_id.get(members[0])
            names[cid] = _headline(first) if first else cid
        else:
            names[cid] = result
    return names


def _headline(fn: Optional[Function]) -> str:
    """함수의 압축 표현 — logical_name 우선, 없으면 summary 의 `[핵심]` 첫 줄.

    군집 이름 입력은 본문(내용) 빼고 `[핵심]` 한 줄만 → 멤버 많아도 토큰 안전.
    """
    if fn is None:
        return ""
    if fn.logical_name:
        return fn.logical_name
    summary = fn.summary or ""
    for line in summary.splitlines():
        line = line.strip()
        if line:
            return line.replace("[핵심]", "").strip()
    return fn.signature or fn.id
