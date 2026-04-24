"""엔티티 식별 — Startpoint 기반 단일 파이프라인.

4가지 시나리오를 배타적 우선순위로 선택하고, 동일한 파이프라인으로 처리한다:
  ① DDL 테이블 (scenario=ddl)
  ② XML/어노테이션 엔티티 FQN (scenario=entity_fqn)
  ③ CLASS/STRUCT 후보 (scenario=candidates)
  ④ Profile 지침만 (scenario=profile_only)

시스템 프롬프트는 모든 시나리오 공통. scenario 는 user message 의 `카테고리:` 힌트로
전달되고, LLM 이 `list_skills()` / `read_skill(name)` 을 호출해 해당 카테고리에 맞는
스킬 본문(`entity-analysis` 또는 `table-tracking`) 을 로드해 처리한다.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from config.settings import settings
from util.llm_audit_logger import log_llm_interaction
from analyzer.strategy.framework.common.node_models import ColumnRef, FkEdge, FunctionTableEdge
from analyzer.strategy.framework.linking.agent.agent_setup import (
    _init_agent_env,
    ToolResultCollector,
    build_tools,
    load_system_prompt,
    load_user_template,
    make_skill_tools,
)
from analyzer.strategy.framework.linking.entity_startpoints import EntityStartpoints

logger = logging.getLogger(__name__)

_BATCH_SIZE = settings.deep_agent.entity_batch_size


# ═══════════════════════════════════════════════════════════════════════
# Public API
# ═══════════════════════════════════════════════════════════════════════


def run_entity_identification(
    startpoints: EntityStartpoints,
    state: Dict[str, Any],
    api_key: Optional[str] = None,
    run_dir: Optional[Path] = None,
) -> Tuple[List[FunctionTableEdge], List[FkEdge], List[ColumnRef]]:
    """Startpoint 기반 엔티티 식별 + Function→Table 연결."""
    scenario = _determine_scenario(startpoints)
    if scenario is None:
        logger.info("[ENTITY] startpoints 전부 비어있음 → 종료")
        return [], [], []

    create_agent, _model = _init_agent_env(state, api_key=api_key, run_dir=run_dir)

    system_prompt = load_system_prompt("linking")
    user_template = load_user_template("linking")

    batches = _build_batches(startpoints, scenario)
    logger.info("[ENTITY] scenario=%s batches=%d", scenario, len(batches))

    collector = ToolResultCollector()
    all_table: List[FunctionTableEdge] = []
    all_fk: List[FkEdge] = []
    all_columns: List[ColumnRef] = []
    failed_batches = 0

    function_count = int(state.get("function_count") or 0)
    for batch_idx, batch in enumerate(batches):
        batch_tools = build_tools(
            state, collector,
            batch_startpoint_tables=batch.table_names,
        ) + make_skill_tools("linking")
        agent = create_agent(tools=batch_tools, system_prompt=system_prompt)
        user_msg = _render_user_message(batch, startpoints, user_template, function_count)

        logger.info("[ENTITY] 배치 %d/%d 시작 | scenario=%s items=%d",
                    batch_idx + 1, len(batches), scenario, len(batch.items))
        invoke_ok = False
        try:
            agent.invoke({"messages": [{"role": "user", "content": user_msg}]})
            log_llm_interaction(
                prompt_name=f"framework/entity/{scenario}",
                input_payload=user_msg,
                output_payload={"total": collector.total_count},
                metadata={"scenario": scenario, "batch": batch_idx, "items": len(batch.items)},
            )
            invoke_ok = True
        except Exception as ex:
            logger.error("[ENTITY] 배치 %d invoke 실패: %s", batch_idx, ex)

        result = collector.snapshot_and_clear()
        if not invoke_ok:
            failed_batches += 1
            dropped = len(result["table_access"]) + len(result["fk"]) + len(result.get("columns", []))
            if dropped:
                logger.warning(
                    "[ENTITY] 실패 배치 부분결과 폐기 | batch=%d dropped=%d",
                    batch_idx, dropped,
                )
            continue
        all_table.extend(result["table_access"])
        all_fk.extend(result["fk"])
        all_columns.extend(result.get("columns", []))

    logger.info(
        "[ENTITY] 완료 | tables=%d READS/WRITES=%d FK=%d columns=%d failed_batches=%d",
        len({e.table_name for e in all_table}), len(all_table), len(all_fk), len(all_columns),
        failed_batches,
    )
    return all_table, all_fk, all_columns


# ═══════════════════════════════════════════════════════════════════════
# 시나리오 정의 (디스패치 테이블 — 우선순위 · 데이터 소스 · 테이블 추출)
# ═══════════════════════════════════════════════════════════════════════


# 각 시나리오의 데이터 소스와 테이블명 추출 로직을 한 곳에 모음.
# 우선순위: 리스트 순서대로. has(sp) 가 True 인 첫 항목이 선택됨.
_SCENARIOS: List[Tuple[str, Callable[[EntityStartpoints], bool],
                       Callable[[EntityStartpoints], list],
                       Callable[[list], Set[str]]]] = [
    (
        "ddl",
        lambda sp: bool(sp.ddl_tables),
        lambda sp: list(sp.ddl_tables),
        lambda chunk: {name for name, _ in chunk},
    ),
    (
        "entity_fqn",
        lambda sp: bool(sp.xml_entity_fqns or sp.annotated_fqns),
        lambda sp: sorted(sp.xml_entity_fqns | sp.annotated_fqns),
        lambda _chunk: set(),
    ),
    (
        "candidates",
        lambda sp: bool(sp.class_candidates),
        lambda sp: list(sp.class_candidates),
        lambda _chunk: set(),
    ),
    (
        "profile_only",
        lambda sp: bool(sp.profile_summary),
        lambda _sp: [None],   # 빈 아이템 하나 — profile_summary 만으로 수색
        lambda _chunk: set(),
    ),
]


def _determine_scenario(sp: EntityStartpoints) -> Optional[str]:
    """우선순위 리스트에서 has(sp) 가 True 인 첫 시나리오 선택."""
    return next((name for name, has, _src, _tbl in _SCENARIOS if has(sp)), None)


# ═══════════════════════════════════════════════════════════════════════
# 배치 생성
# ═══════════════════════════════════════════════════════════════════════


class _Batch:
    __slots__ = ("scenario", "items", "table_names")

    def __init__(self, scenario: str, items: list, table_names: Set[str]):
        self.scenario = scenario
        self.items = items
        self.table_names = table_names


def _build_batches(sp: EntityStartpoints, scenario: str) -> List[_Batch]:
    """디스패치 테이블에서 데이터 소스·테이블 추출 함수를 찾아 일괄 배치."""
    _name, _has, source_fn, table_fn = next(
        (entry for entry in _SCENARIOS if entry[0] == scenario)
    )
    source = source_fn(sp)
    if not source:
        return [_Batch(scenario, [], set())]
    return [
        _Batch(scenario, source[i:i + _BATCH_SIZE], table_fn(source[i:i + _BATCH_SIZE]))
        for i in range(0, len(source), _BATCH_SIZE)
    ]


# ═══════════════════════════════════════════════════════════════════════
# User Message 렌더링 — 시나리오 분기 없이 통일 포맷
# ═══════════════════════════════════════════════════════════════════════


def _extract_existing_fks(ddl_tables: list) -> list:
    """DDL 메타에서 기존 FK 목록 추출 (ddl 시나리오 외에서는 빈 리스트)."""
    return [
        {"from": f"{name}.{fk.get('column', '')}", "to": fk.get("ref", "")}
        for name, meta in ddl_tables
        for fk in (meta.get("foreign_keys") or meta.get("foreignKeys") or [])
        if fk.get("column") and fk.get("ref")
    ][:50]


def _render_user_message(batch: _Batch, sp: EntityStartpoints, user_template: str, function_count: int = 0) -> str:
    """시나리오 힌트(category) + 원본 데이터(data) + 프로파일 지침(profile_hint) 을 채운다.

    - `data`: JSON — 아이템 + 시나리오별 보조 맥락(existing_fks, xml_entity_hints).
    - `profile_hint`: 평문 — profile_summary 가 있으면 prose 섹션으로 렌더.
      LLM 이 JSON 파싱 없이 바로 읽을 수 있도록 평문으로 분리 (profile_only 시나리오 특히 중요).
    """
    data = {
        "scenario": batch.scenario,
        "items": batch.items,
        "context": {
            "existing_fks": _extract_existing_fks(list(sp.ddl_tables)),
            "xml_entity_hints": sorted(sp.xml_entity_fqns | sp.annotated_fqns)[:30],
        },
    }
    profile_hint = ""
    if sp.profile_summary:
        profile_hint = (
            "\n---\n"
            "[Project Framework · DB Access Pattern Analysis — Search Map]\n\n"
            f"{sp.profile_summary}\n"
        )

    return user_template.format(
        category=batch.scenario,
        data=json.dumps(data, ensure_ascii=False, default=str),
        profile_hint=profile_hint,
    )
