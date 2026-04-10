"""DeepAgent 미해결 참조 추적 (Unresolved Reference Tracker).

미매칭된 인덱스 키(SQL, Config, Entity, Bean 등)를 DeepAgent로 처리하여
코드 내 실제 사용처를 찾고 그래프 관계를 생성한다.

[설계 원칙]
  1. Skill 기반: 입력 카테고리에 맞는 SKILL.md를 로드하여 시스템 프롬프트 구성
  2. 배치 처리: 같은 카테고리의 아이템을 묶어서 에이전트 1회 호출로 처리
  3. 에이전트 재사용: 같은 Skill의 배치는 에이전트를 재생성하지 않음
  4. 즉시 반영: 도구 호출 결과를 ToolResultCollector에 수집,
     phase_linking에서 배치 단위로 Neo4j에 즉시 반영

[처리 흐름]
  UnresolvedLink 목록
  → 카테고리별 그룹핑
  → 각 카테고리: Skill 선택 → 에이전트 생성 → 배치 invoke
  → ToolResultCollector에 결과 누적
  → Generator로 yield → phase_linking에서 Neo4j 반영
"""

from __future__ import annotations

import json
import logging
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, Generator, List, Optional, Tuple

from config.settings import settings
from util.artifact_logger import log_llm_interaction
from analyzer.strategy.framework.common.models import (
    FkEdge,
    FunctionTableEdge,
    ReferToEdge,
    UnresolvedLink,
)
from analyzer.strategy.framework.linking.agent.common import _init_deep_agent_env
from analyzer.strategy.framework.linking.agent.tools import (
    ToolResultCollector,
    build_tools,
)
from analyzer.strategy.framework.linking.agent.skills import (
    SkillInfo,
    build_system_prompt_with_skill,
    load_all_skills,
    select_skill_for_category,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 상수
# ---------------------------------------------------------------------------

# 같은 카테고리 아이템을 묶는 배치 크기
_UNRESOLVED_BATCH_SIZE = 10

# 배치당 최대 처리 시간 (초)
_BATCH_TIMEOUT = 300


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

# 반환 타입 alias
_YieldType = Tuple[List[ReferToEdge], List[FunctionTableEdge], List[FkEdge]]


def run_deep_agent(
    unresolved_list: List[UnresolvedLink],
    state: Dict[str, Any],
    api_key: Optional[str] = None,
    run_dir: Optional[Path] = None,
) -> Generator[_YieldType, None, None]:
    """미해결 참조 목록을 DeepAgent로 처리한다.

    처리 흐름:
      1. Skill 전체 로드 (SKILL.md 4종)
      2. UnresolvedLink를 카테고리별로 그룹핑
      3. 각 카테고리 그룹을 배치 단위로 에이전트 invoke
      4. 배치 완료마다 결과 yield (→ phase_linking에서 Neo4j 즉시 반영)

    Args:
        unresolved_list: UnresolvedLink 목록.
        state: pipeline state (인덱스, DDL 메타 등).
        api_key: LLM API 키.
        run_dir: 실행 디렉토리.

    Yields:
        (refer_to_edges, function_table_edges, fk_edges) — 배치 단위.
    """
    if not unresolved_list:
        logger.info("[DEEP_AGENT] unresolved 없음 → 즉시 종료")
        return

    # ── 1. 초기화 ──
    try:
        create_deep_agent, model = _init_deep_agent_env(state, api_key=api_key, run_dir=run_dir)
    except Exception as e:
        logger.error("[DEEP_AGENT] 초기화 실패: %s", e)
        return

    skills = load_all_skills()
    if not skills:
        logger.warning("[DEEP_AGENT] SKILL.md 없음 → 폴백 프롬프트 사용")

    # ── 2. 카테고리별 그룹핑 ──
    groups = _group_by_category(unresolved_list)
    total = len(unresolved_list)
    logger.info(
        "[DEEP_AGENT] 시작 | total=%d categories=%s",
        total, {k: len(v) for k, v in groups.items()},
    )

    # ── 3. 카테고리별 처리 ──
    for category, items in groups.items():
        # Skill 선택
        skill = select_skill_for_category(category, skills)
        skill_name = skill.name if skill else ""
        system_prompt = build_system_prompt_with_skill(skill) if skill else _fallback_system_prompt()

        # 배치 분할
        batches = [items[i:i + _UNRESOLVED_BATCH_SIZE] for i in range(0, len(items), _UNRESOLVED_BATCH_SIZE)]
        logger.info(
            "[DEEP_AGENT] category=%s | items=%d batches=%d skill=%s",
            category, len(items), len(batches), skill_name,
        )

        # 에이전트 생성 (카테고리당 1회)
        collector = ToolResultCollector()
        tools = build_tools(state, collector, skill_name=skill_name)
        agent = create_deep_agent(model=model, tools=tools, system_prompt=system_prompt)

        # 배치별 실행
        for batch_idx, batch in enumerate(batches):
            user_message = _build_batch_user_message(batch)
            try:
                agent.invoke({"messages": [{"role": "user", "content": user_message}]})
                log_llm_interaction(
                    prompt_name=f"framework/deep_agent/{skill_name or 'fallback'}",
                    input_payload=user_message[:500],
                    output_payload={"total": collector.total_count},
                    metadata={"category": category, "batch": batch_idx, "items": len(batch)},
                )
            except Exception as ex:
                logger.error("[DEEP_AGENT] invoke 실패 | category=%s batch=%d error=%s", category, batch_idx, ex)

            # 배치 결과 yield (→ Neo4j 즉시 반영)
            result = collector.snapshot_and_clear()
            if result["refer_to"] or result["table_access"] or result["fk"]:
                logger.info(
                    "[DEEP_AGENT] 배치 완료 | category=%s batch=%d refer=%d table=%d fk=%d",
                    category, batch_idx, len(result["refer_to"]), len(result["table_access"]), len(result["fk"]),
                )
                yield (result["refer_to"], result["table_access"], result["fk"])

    logger.info("[DEEP_AGENT] 전체 완료 | total=%d", total)


# ---------------------------------------------------------------------------
# 내부 함수
# ---------------------------------------------------------------------------


def _group_by_category(items: List[UnresolvedLink]) -> Dict[str, List[UnresolvedLink]]:
    """UnresolvedLink를 ref_candidates의 category별로 그룹핑한다.

    Returns:
        {"entity": [...], "sql": [...], "config": [...], ...}
    """
    groups: Dict[str, List[UnresolvedLink]] = {}
    for u in items:
        info = u.ref_candidates[0] if u.ref_candidates else {}
        category = info.get("category", "other") if isinstance(info, dict) else "other"
        groups.setdefault(category, []).append(u)
    return groups


def _build_batch_user_message(batch: List[UnresolvedLink]) -> str:
    """배치 내 UnresolvedLink 목록을 하나의 user message로 구성한다.

    각 아이템을 번호 매겨서 나열하고, 에이전트가 순서대로 처리하도록 한다.
    """
    parts: List[str] = []
    parts.append(f"아래 {len(batch)}개 아이템을 순서대로 처리하세요.\n")

    for idx, u in enumerate(batch, start=1):
        parts.append(f"─── 아이템 {idx}/{len(batch)} ───")
        parts.append(_build_single_item_message(u))
        parts.append("")

    parts.append("모든 아이템을 처리했으면 종료하세요.")
    return "\n".join(parts)


def _build_single_item_message(u: UnresolvedLink) -> str:
    """단일 UnresolvedLink에 대한 메시지를 생성한다.

    function_id 유무에 따라 두 가지 형식:
    - function_id 있음: 함수 기준 추적 (ref_candidates 정보 포함)
    - function_id 없음: 미매칭 키 추적 (카테고리별 상세 지시)
    """
    has_function = bool(u.function_id and u.function_id.strip())

    if has_function:
        return (
            f"function_id: {u.function_id}\n"
            f"ref_candidates: {json.dumps(u.ref_candidates, ensure_ascii=False, default=str)}"
        )

    # 미매칭 키: 카테고리별 상세 메시지
    info = u.ref_candidates[0] if u.ref_candidates else {}
    category = info.get("category", "")
    key = info.get("key", "")
    target_type = info.get("target_type", "")

    header = f"미매칭 인덱스 키 (category: {category})\n"
    header += f"target_type: {target_type}\nkey: {key}\n"

    if category in ("entity", "bean", "cmp_mapping"):
        header += (
            f"chunk_type: {info.get('chunk_type', '')}\n"
            f"file_path: {info.get('file_path', '')}\n"
            f"[정의 원문]\n{info.get('text_snippet', '')}\n\n"
            f"위 원문에서 식별자를 파악한 뒤, 이 청크를 사용하는 메서드를 찾으세요.\n"
            f"1) 식별자로 bundle_search → 사용처 추적\n"
            f"2) 사용 패턴 판별 (CRUD, 호출, 참조 등)\n"
            f"3) graph_create_table_access_tool 등록 (source_function_id 필수)\n"
        )
    elif category == "sql":
        header += (
            f"sql_type: {info.get('sql_type', '')}\n"
            f"file_path: {info.get('file_path', '')}\n\n"
            f"이 SQL을 실행하는 메서드를 찾으세요.\n"
            f"1) sql_key '{key}' 또는 파일명/네임스페이스로 bundle_search\n"
            f"2) graph_create_refer_to_tool 등록 (source_function_id 필수)\n"
        )
    elif category == "config":
        header += (
            f"file_path: {info.get('file_path', '')}\n\n"
            f"이 설정을 참조하는 메서드를 찾으세요.\n"
            f"1) 설정 키 '{key}' 또는 파일명으로 bundle_search\n"
            f"2) graph_create_refer_to_tool 등록 (source_function_id 필수)\n"
        )
    else:
        header += f"\ninfo: {json.dumps(info, ensure_ascii=False, default=str)}\n"

    return header


def _fallback_system_prompt() -> str:
    """SKILL.md가 없을 때의 폴백 시스템 프롬프트."""
    return (
        "당신은 소스코드 분석 전문가 에이전트입니다.\n\n"
        "[절대 규칙]\n"
        "- 오직 제공된 도구만 사용하십시오.\n"
        "- 확신이 없으면 관계를 생성하지 마십시오.\n"
        "- 하나를 찾았다고 멈추지 말고 모든 사용처를 빠짐없이 찾으십시오.\n"
        "- 검색 결과가 0건이면 검색어를 바꿔 최소 2~3회 재시도하십시오.\n"
        "- source_function_id는 반드시 bundle_search에서 발견한 실제 함수의 chunk_id를 사용하십시오.\n"
    )
