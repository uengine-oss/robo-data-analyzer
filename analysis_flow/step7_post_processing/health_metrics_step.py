"""health_metrics_step — 코드 건강지표 (결정론·LLM 0).

robo 가 이미 만든 그래프(state 의 code_nodes / calls_edges / reads_writes_edges)만으로
코드노드별 위험 지표를 계산해 Neo4j FUNCTION 노드 **속성**으로 적재 + 요약 리포트 산출.

지표 (전부 결정론 — pure Python, LLM/외부 의존성 0):
- ``fan_in`` / ``fan_out``  : 내부 호출(scope=internal) in/out 차수 (의존 집중도)
- ``loc``                   : end_line - start_line + 1 (코드 규모)
- ``in_cycle``              : 순환 호출 포함 여부 — ``call_cycles.find_call_cycles`` 재사용
- ``table_coupling``        : R/W 하는 distinct 테이블 수 (데이터 결합도)
- ``god_flag``              : 거대 코드노드 후보 (LOC 큼 + fan_out 큼)
- ``risk``                  : low / medium / high 종합 라벨

★ 본 로직(step1~6·DB·기존 step7 분석) 무수정 — state 를 읽어 계산하고 Neo4j 에 속성만 SET.
   embed_step 과 동일 패턴(읽기→배치 UNWIND SET). 새 노드/엣지/의존성 없음.
"""
from __future__ import annotations

import json
import time
from collections import defaultdict
from typing import Optional

from langchain_core.runnables import RunnableConfig

from analysis_flow.step2_load_ast.antlr_json_reader import _ROUTINE_LABELS
from analysis_flow.step6_analyze_code.call_cycles import find_call_cycles
from data_models.graph_edges import Calls, ReadsWrites
from data_models.graph_nodes import CodeNode
from shared.config.env_catalog import ENV
from shared.log.logger import log_info, log_warning
from shared.neo4j.neo4j_client import Neo4jClient
from shared.workflow.step_utils import step_config


_STEP = "step7.health_metrics"

# 휴리스틱 임계값 (튜닝 가능 — 레거시 규모에 맞춰 조정).
GOD_LOC_THRESHOLD = 300        # 이 줄수 이상이면 거대 코드노드 후보
GOD_FANOUT_THRESHOLD = 15      # 이만큼 많은 코드노드를 호출하면 오케스트레이터 의심
HUB_FANIN_THRESHOLD = 20       # 이만큼 많이 불리면 핵심 허브 (변경 파급 큼)
TABLE_COUPLING_THRESHOLD = 5   # 이만큼 많은 테이블을 만지면 데이터 결합 과다


# 저장은 최종 판정만 (spec 020): risk_level(+in_call_cycle). 중간 지표(loc·fan·god·table_coupling)는
# 계산·리포트엔 쓰되 노드에 안 박는다. fan 은 CALLS 관계로 즉석 파생. 루틴 노드에만 SET.
_SET_QUERY = """
UNWIND $items AS item
MATCH (node {id: item.id})
SET node += item
"""


async def health_metrics_step(state: dict, config: Optional[RunnableConfig] = None) -> dict:
    """state 그래프 → 코드노드별 건강지표 계산 → Neo4j 속성 SET + 요약 리포트.

    LLM·외부 의존성 0. neo_client 없으면 계산·리포트만 (SET skip).
    state 는 변경하지 않는다(키 충돌 회피) — Neo4j 가 단일 진실, 리포트는 보조 산출.
    """
    cfg = step_config(config)
    neo: Neo4jClient | None = cfg.get("neo_client")

    code_nodes: list[CodeNode] = state.get("code_nodes", [])
    if not code_nodes:
        log_info(_STEP, "skip", reason="code_nodes 0")
        return {}

    calls: list[Calls] = state.get("calls_edges", [])
    reads_writes: list[ReadsWrites] = state.get("reads_writes_edges", [])
    started = time.monotonic()
    log_info(_STEP, "start", code_nodes=len(code_nodes), calls=len(calls), rw=len(reads_writes))

    items = _compute_metrics(code_nodes, calls, reads_writes)

    # 저장 item — 루틴 노드에만 risk_level(+in_call_cycle=True 일 때만). 중간지표는 미저장.
    neo_items = []
    for f, it in zip(code_nodes, items):
        if f.label not in _ROUTINE_LABELS:
            continue
        ni = {"id": it["id"], "risk_level": it["risk"]}
        if it["in_cycle"]:
            ni["in_call_cycle"] = True
        neo_items.append(ni)

    if neo is not None:
        await neo.run_batch_unwind(_SET_QUERY, neo_items)
    else:
        log_warning(_STEP, "config_missing", "neo_client 누락 — Neo4j SET skip (계산·리포트만)")

    _write_report(items)

    high = sum(1 for it in items if it["risk"] == "high")
    log_info(
        _STEP, "done",
        code_nodes=len(items), high_risk=high,
        in_cycle=sum(1 for it in items if it["in_cycle"]),
        god=sum(1 for it in items if it["god_flag"]),
        elapsed_sec=f"{time.monotonic() - started:.2f}",
    )
    return {}


def _compute_metrics(
    code_nodes: list[CodeNode],
    calls: list[Calls],
    reads_writes: list[ReadsWrites],
) -> list[dict]:
    """결정론 계산 — 코드노드별 지표 dict 리스트 (Neo4j SET item 형식)."""
    ids = {f.id for f in code_nodes}

    # 내부 호출만 (external = 분석 범위 밖 stub → 차수·순환 계산서 제외).
    internal_edges = [
        (c.caller_id, c.callee_id)
        for c in calls
        if c.scope == "internal" and c.caller_id in ids and c.callee_id in ids
    ]

    fan_out_sets: dict[str, set[str]] = defaultdict(set)
    fan_in_sets: dict[str, set[str]] = defaultdict(set)
    for caller, callee in internal_edges:
        fan_out_sets[caller].add(callee)
        fan_in_sets[callee].add(caller)

    # 순환 — 기존 Tarjan SCC 재사용 (cycle 안이면 mapping 에 존재).
    cycle_map = find_call_cycles(internal_edges, ids)

    # 데이터 결합도 — 코드노드가 만지는 distinct 테이블 수.
    table_sets: dict[str, set[str]] = defaultdict(set)
    for rw in reads_writes:
        if rw.source_id in ids:
            table_sets[rw.source_id].add(rw.target_id)

    items: list[dict] = []
    for f in code_nodes:
        fid = f.id
        fan_out = len(fan_out_sets.get(fid, ()))
        fan_in = len(fan_in_sets.get(fid, ()))
        loc = (f.end_line - f.start_line + 1) if (f.end_line and f.start_line and f.end_line >= f.start_line) else 0
        in_cycle = fid in cycle_map
        table_coupling = len(table_sets.get(fid, ()))
        god_flag = loc >= GOD_LOC_THRESHOLD and fan_out >= GOD_FANOUT_THRESHOLD
        items.append({
            "id": fid,
            "fan_in": fan_in,
            "fan_out": fan_out,
            "loc": loc,
            "in_cycle": in_cycle,
            "table_coupling": table_coupling,
            "god_flag": god_flag,
            "risk": _risk_label(loc, fan_in, fan_out, in_cycle, god_flag, table_coupling),
        })
    return items


def _risk_label(loc: int, fan_in: int, fan_out: int, in_cycle: bool,
                god_flag: bool, table_coupling: int) -> str:
    """종합 위험 라벨 — 순환/거대 = high, 단일 경고 신호 = medium, 그 외 low."""
    if in_cycle or god_flag:
        return "high"
    if (loc >= GOD_LOC_THRESHOLD or fan_out >= GOD_FANOUT_THRESHOLD
            or fan_in >= HUB_FANIN_THRESHOLD or table_coupling >= TABLE_COUPLING_THRESHOLD):
        return "medium"
    return "low"


def _write_report(items: list[dict]) -> None:
    """요약 리포트 → ``ENV.log.dir/analysis.run/latest/health_report.json`` (graceful).

    저장만으론 안 보이므로 사람·UI 가 볼 요약 산출 (소비처 확보). 적재 실패는 분석 흐름
    막지 X — 사유 명시 후 skip.
    """
    high = [it for it in items if it["risk"] == "high"]
    report = {
        "total_code_nodes": len(items),
        "risk": {
            "high": sum(1 for it in items if it["risk"] == "high"),
            "medium": sum(1 for it in items if it["risk"] == "medium"),
            "low": sum(1 for it in items if it["risk"] == "low"),
        },
        "in_cycle": sum(1 for it in items if it["in_cycle"]),
        "god_code_nodes": sum(1 for it in items if it["god_flag"]),
        # 위험 상위 — fan_in·loc 큰 순 (변경 파급·규모).
        "top_risky": sorted(
            high, key=lambda it: (it["fan_in"], it["loc"]), reverse=True,
        )[:20],
        "thresholds": {
            "god_loc": GOD_LOC_THRESHOLD,
            "god_fanout": GOD_FANOUT_THRESHOLD,
            "hub_fanin": HUB_FANIN_THRESHOLD,
            "table_coupling": TABLE_COUPLING_THRESHOLD,
        },
    }
    try:
        path = ENV.log.dir / "analysis.run" / "latest" / "health_report.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        log_info(_STEP, "report", path=str(path))
    except Exception as e:  # noqa: BLE001 — 리포트 적재 실패는 분석 흐름 막지 X
        log_warning(_STEP, "report", f"health_report.json 적재 실패 (계산·SET 은 성공) — {type(e).__name__}: {e}")
