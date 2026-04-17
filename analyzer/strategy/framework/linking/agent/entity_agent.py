"""엔티티 식별 — Startpoint 기반 단일 파이프라인.

4가지 시나리오를 배타적 우선순위로 선택하고,
동일한 파이프라인(배치 분할 → user message 조립 → agent 실행)으로 처리한다.

시나리오 우선순위:
  ① DDL 테이블 → table-tracking Skill
  ② XML/어노테이션 엔티티 FQN → entity-analysis Skill
  ③ CLASS/STRUCT 후보 → entity-analysis Skill
  ④ Profile 지침만 → entity-analysis Skill
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from config.settings import settings
from util.llm_audit_logger import log_llm_interaction
from analyzer.strategy.framework.common.node_models import ColumnRef, FkEdge, FunctionTableEdge
from analyzer.strategy.framework.linking.agent.agent_setup import (
    _init_agent_env,
    ToolResultCollector,
    build_tools,
    build_system_prompt_with_skill,
    load_all_skills,
    select_skill_for_category,
)
from analyzer.strategy.framework.linking.entity_startpoints import EntityStartpoints

logger = logging.getLogger(__name__)

_BATCH_SIZE = settings.deep_agent.entity_batch_size

# 시나리오 → skill 카테고리 매핑
_SCENARIO_SKILL = {
    "ddl": "ddl_tables",
    "entity_fqn": "entity",
    "candidates": "entity",
    "profile_only": "entity",
}


# ═══════════════════════════════════════════════════════════════════════
# Public API
# ═══════════════════════════════════════════════════════════════════════


def run_entity_identification(
    startpoints: EntityStartpoints,
    state: Dict[str, Any],
    api_key: Optional[str] = None,
    run_dir: Optional[Path] = None,
) -> Tuple[List[FunctionTableEdge], List[FkEdge], List[ColumnRef]]:
    """Startpoint 기반 엔티티 식별 + Function→Table 연결.

    Returns:
        (function_table_edges, fk_edges, column_refs)
    """
    scenario = _determine_scenario(startpoints)
    if scenario is None:
        logger.info("[ENTITY] startpoints 전부 비어있음 → 종료")
        return [], [], []

    # 초기화
    create_agent, model = _init_agent_env(state, api_key=api_key, run_dir=run_dir)
    skills = load_all_skills()
    skill = select_skill_for_category(_SCENARIO_SKILL[scenario], skills)
    system_prompt = _build_system_prompt(skill, startpoints.profile_summary)

    # 배치 생성
    batches = _build_batches(startpoints, scenario)
    logger.info("[ENTITY] scenario=%s batches=%d", scenario, len(batches))

    # 배치별 실행
    collector = ToolResultCollector()
    skill_name = skill.name if skill else ""

    for batch_idx, batch in enumerate(batches):
        batch_tools = build_tools(
            state, collector, skill_name=skill_name,
            batch_startpoint_tables=batch.table_names,
        )
        agent = create_agent(tools=batch_tools, system_prompt=system_prompt)
        user_msg = _render_user_message(batch, startpoints)

        logger.info("[ENTITY] 배치 %d/%d 시작 | scenario=%s items=%d",
                    batch_idx + 1, len(batches), scenario, len(batch.items))
        try:
            agent.invoke({"messages": [{"role": "user", "content": user_msg}]})
            log_llm_interaction(
                prompt_name=f"framework/entity/{skill_name or 'fallback'}",
                input_payload=user_msg[:500],
                output_payload={"total": collector.total_count},
                metadata={"scenario": scenario, "batch": batch_idx, "items": len(batch.items)},
            )
        except Exception as ex:
            logger.error("[ENTITY] 배치 %d invoke 실패: %s", batch_idx, ex)

    # 결과 반환
    result = collector.snapshot_and_clear()
    all_table = result["table_access"]
    all_fk = result["fk"]
    all_columns = result.get("columns", [])

    logger.info(
        "[ENTITY] 완료 | tables=%d READS/WRITES=%d FK=%d columns=%d",
        len({e.table_name for e in all_table}), len(all_table), len(all_fk), len(all_columns),
    )
    return all_table, all_fk, all_columns


# ═══════════════════════════════════════════════════════════════════════
# 시나리오 결정
# ═══════════════════════════════════════════════════════════════════════


def _determine_scenario(sp: EntityStartpoints) -> Optional[str]:
    """우선순위 기반 배타적 선택."""
    if sp.ddl_tables:
        return "ddl"
    if sp.xml_entity_fqns or sp.annotated_fqns:
        return "entity_fqn"
    if sp.class_candidates:
        return "candidates"
    if sp.profile_summary:
        return "profile_only"
    return None


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
    if scenario == "ddl":
        tables = list(sp.ddl_tables)
        return [
            _Batch("ddl", tables[i:i+_BATCH_SIZE],
                   {name for name, _ in tables[i:i+_BATCH_SIZE]})
            for i in range(0, len(tables), _BATCH_SIZE)
        ]
    if scenario == "entity_fqn":
        fqns = sorted(sp.xml_entity_fqns | sp.annotated_fqns)
        return [
            _Batch("entity_fqn", fqns[i:i+_BATCH_SIZE], set())
            for i in range(0, len(fqns), _BATCH_SIZE)
        ]
    if scenario == "candidates":
        cands = sp.class_candidates
        return [
            _Batch("candidates", cands[i:i+_BATCH_SIZE], set())
            for i in range(0, len(cands), _BATCH_SIZE)
        ]
    return [_Batch("profile_only", [], set())]


# ═══════════════════════════════════════════════════════════════════════
# System Prompt
# ═══════════════════════════════════════════════════════════════════════


def _build_system_prompt(skill: Any, profile_summary: str) -> str:
    prompt = build_system_prompt_with_skill(skill) if skill else _default_prompt()
    if profile_summary:
        prompt += (
            "\n\n---\n"
            "[프로젝트 프레임워크·DB 접근 패턴 분석 — 수색 지침]\n\n"
            f"{profile_summary}\n"
        )
    return prompt


def _default_prompt() -> str:
    try:
        from util.llm_prompt_handler import prompts
        return prompts.deep_agent_entity_prompt()
    except Exception:
        return "소스코드에서 DB 엔티티를 식별하고 각 함수의 테이블 접근을 추적하는 전문가 에이전트입니다."


# ═══════════════════════════════════════════════════════════════════════
# User Message 렌더링
# ═══════════════════════════════════════════════════════════════════════


def _render_user_message(batch: _Batch, sp: EntityStartpoints) -> str:
    parts: List[str] = []

    if batch.scenario == "ddl":
        parts.append(_render_ddl(batch.items, sp))
    elif batch.scenario == "entity_fqn":
        parts.append(_render_entity_fqn(batch.items, sp))
    elif batch.scenario == "candidates":
        parts.append(_render_candidates(batch.items))
    else:
        parts.append(_render_profile_only())

    # 공통 suffix: 컬럼 지침
    if batch.scenario == "ddl":
        parts.append("\n[컬럼 지침]\n컬럼은 DDL에서 이미 확보됨. graph_create_column_tool 사용 금지.")
    else:
        parts.append(
            "\n[컬럼 지침]\n"
            "READS/WRITES가 확정된 함수에서 사용하는 구조체/DTO 필드만 컬럼으로 등록. "
            "테이블 연결 증거 없는 필드 등록 금지."
        )

    return "\n".join(parts)


def _render_ddl(items: list, sp: EntityStartpoints) -> str:
    parts: List[str] = []

    # 기존 FK (중복 방지)
    fk_lines: List[str] = []
    for name, meta in items:
        for fk in (meta.get("foreign_keys") or meta.get("foreignKeys") or []):
            fk_col = fk.get("column", "")
            fk_ref = fk.get("ref", "")
            if fk_col and fk_ref:
                fk_lines.append(f"- {name}.{fk_col} → {fk_ref}")
    if fk_lines:
        parts.append("[이미 식별된 FK — 중복 생성 금지]\n" + "\n".join(fk_lines[:50]))

    # 테이블 목록
    table_names = [name for name, _ in items]
    parts.append("[시작점: DDL 테이블]\n" + ", ".join(table_names))

    # XML 엔티티 힌트 (DDL↔XML 매칭은 DeepAgent 몫)
    xml_fqns = sp.xml_entity_fqns | sp.annotated_fqns
    if xml_fqns:
        parts.append(
            "[XML/어노테이션 엔티티 힌트]\n"
            + ", ".join(sorted(xml_fqns)[:30]) + "\n"
            "(DDL 테이블과의 매칭은 코드를 보면서 판단하세요.)"
        )

    parts.append("위 테이블을 코드에서 사용하는 함수를 추적하세요.")
    return "\n\n".join(parts)


def _render_entity_fqn(items: list, sp: EntityStartpoints) -> str:
    lines = ["[시작점: 엔티티 FQN]"]
    for fqn in items:
        source = "XML" if fqn in sp.xml_entity_fqns else "annotation"
        lines.append(f"- {fqn} (source: {source})")
    lines.append("\n위 엔티티를 코드에서 사용하는 함수를 추적하세요.")
    return "\n".join(lines)


def _render_candidates(items: list) -> str:
    lines = ["[시작점: 후보 클래스/구조체]"]
    for c in items:
        line = f"- {c['fqn']} (file: {c['file_path']})"
        if c.get("annotations"):
            line += f" [annotations: {c['annotations']}]"
        if c.get("comment"):
            line += f" [comment: {c['comment']}]"
        lines.append(line)
    lines.append("\n위 후보에서 DB 엔티티를 식별하고, 사용하는 함수를 추적하세요.")
    return "\n".join(lines)


def _render_profile_only() -> str:
    return (
        "[시작점 없음 — 프로파일 지침 기반 수색]\n"
        "엔티티 후보가 AST에 없습니다. 아래 프레임워크/DB 접근 패턴 분석을 지침으로 삼아 "
        "코드에서 테이블을 식별하세요."
    )
