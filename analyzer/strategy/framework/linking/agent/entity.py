"""DeepAgent 엔티티 식별 (Entity Identification).

두 가지 시나리오를 Skill 기반으로 처리한다:
  1. DDL 있음 (table-tracking): 테이블 목록 → 코드 역추적 → READS/WRITES + FK
  2. DDL 없음 (entity-analysis): CLASS/STRUCT 후보 → 엔티티 판별 → READS/WRITES + FK

[설계 원칙]
  - 카테고리당 에이전트 1개 (재사용)
  - 배치 처리로 LLM 호출 최소화
  - ToolResultCollector로 결과 수집 → 즉시 반환
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from config.settings import settings
from util.artifact_logger import log_llm_interaction
from analyzer.strategy.framework.common.models import FkEdge, FunctionTableEdge, ReferToEdge
from analyzer.strategy.framework.linking.agent.common import _init_deep_agent_env
from analyzer.strategy.framework.linking.agent.tools import ToolResultCollector, build_tools
from analyzer.strategy.framework.linking.agent.skills import (
    build_system_prompt_with_skill,
    load_all_skills,
    select_skill_for_category,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 상수
# ---------------------------------------------------------------------------

# 엔티티 후보 배치 크기
_ENTITY_BATCH_SIZE_DEFAULT = 10

# 엔티티 후보로 취급할 AST 노드 타입
_ENTITY_CANDIDATE_KINDS = frozenset({"CLASS", "STRUCT", "UNION"})

# 엔티티 디렉토리 힌트 (경로 기반 사전 분류)
_ENTITY_DIR_HINTS = frozenset({"entity", "entities", "model", "models", "domain", "schema"})
_DTO_DIR_HINTS = frozenset({"dto", "vo", "request", "response", "view"})


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def run_deep_agent_entity_identification(
    module_index: Dict[str, Any],
    state: Dict[str, Any],
    api_key: Optional[str] = None,
    run_dir: Optional[Path] = None,
    ddl_table_metadata: Optional[Dict] = None,
) -> Tuple[List[FunctionTableEdge], List[FkEdge]]:
    """DeepAgent로 엔티티 식별 + Function→Table 연결을 수행한다.

    시나리오 분기:
      - DDL 있음: table-tracking Skill → 테이블별 코드 역추적
      - DDL 없음: entity-analysis Skill → CLASS/STRUCT 후보 판별 → 추적

    Args:
        module_index: {fqn: {kind, name, file_path, ...}} 모듈 인덱스.
        state: pipeline state (인덱스, DDL 메타 등).
        api_key: LLM API 키.
        run_dir: 실행 디렉토리.
        ddl_table_metadata: DDL 테이블 메타데이터.

    Returns:
        (function_table_edges, fk_edges)
    """
    has_ddl = bool(ddl_table_metadata)
    has_candidates = bool(module_index)

    if not has_ddl and not has_candidates:
        logger.info("[ENTITY] DDL도 후보도 없음 → 즉시 종료")
        return [], []

    # ── 초기화 ──
    try:
        create_deep_agent, model = _init_deep_agent_env(state, api_key=api_key, run_dir=run_dir)
    except Exception as e:
        logger.error("[ENTITY] 초기화 실패: %s", e)
        return [], []

    # ── Skill 선택 ──
    skills = load_all_skills()
    skill_category = "ddl_tables" if has_ddl else "entity"
    skill = select_skill_for_category(skill_category, skills)
    skill_name = skill.name if skill else ""
    system_prompt = build_system_prompt_with_skill(skill) if skill else _default_entity_prompt(has_ddl)

    # ── Collector + 도구 생성 ──
    collector = ToolResultCollector()
    tools = build_tools(state, collector, skill_name=skill_name)
    agent = create_deep_agent(model=model, tools=tools, system_prompt=system_prompt)

    # ── 시나리오별 실행 ──
    if has_ddl:
        _run_ddl_scenario(agent, collector, ddl_table_metadata, skill_name)
    else:
        _run_candidates_scenario(
            agent, collector, create_deep_agent, model, tools,
            system_prompt, module_index, skill_name,
        )

    # ── 결과 반환 ──
    # 남은 결과 수집
    final = collector.snapshot_and_clear()
    all_table = final["table_access"]
    all_fk = final["fk"]

    identified_tables = {e.table_name for e in all_table if e.table_name}
    logger.info(
        "[ENTITY] 완료 | tables=%d READS/WRITES=%d FK=%d",
        len(identified_tables), len(all_table), len(all_fk),
    )
    return all_table, all_fk


# ---------------------------------------------------------------------------
# 시나리오별 처리
# ---------------------------------------------------------------------------


def _run_ddl_scenario(
    agent: Any,
    collector: ToolResultCollector,
    ddl_table_metadata: Dict,
    skill_name: str,
) -> None:
    """DDL 시나리오: 테이블 목록으로 코드 역추적."""
    user_msg = _build_ddl_user_message(ddl_table_metadata)
    logger.info("[ENTITY:DDL] 테이블 %d개로 코드 추적 시작", len(ddl_table_metadata))

    try:
        agent.invoke({"messages": [{"role": "user", "content": user_msg}]})
        log_llm_interaction(
            prompt_name=f"framework/deep_agent/{skill_name}",
            input_payload=user_msg[:500],
            output_payload={"total": collector.total_count},
            metadata={"scenario": "ddl_tables"},
        )
    except Exception as ex:
        logger.error("[ENTITY:DDL] invoke 실패: %s", ex)


def _run_candidates_scenario(
    agent: Any,
    collector: ToolResultCollector,
    create_deep_agent: Any,
    model: Any,
    tools: list,
    system_prompt: str,
    module_index: Dict[str, Any],
    skill_name: str,
) -> None:
    """후보 시나리오: CLASS/STRUCT 후보를 배치로 엔티티 판별."""
    candidates = _extract_entity_candidates(module_index)
    if not candidates:
        logger.info("[ENTITY:CANDIDATES] 후보 없음 → 종료")
        return

    batch_size = settings.deep_agent.entity_batch_size
    batches = [candidates[i:i + batch_size] for i in range(0, len(candidates), batch_size)]
    logger.info("[ENTITY:CANDIDATES] 후보 %d개 → %d배치 처리", len(candidates), len(batches))

    for batch_idx, batch in enumerate(batches):
        user_msg = _build_candidates_user_message(batch)
        batch_names = [c["name"] for c in batch[:5]]
        logger.info("[ENTITY:CANDIDATES] 배치 %d 시작 | %s", batch_idx, batch_names)

        try:
            agent.invoke({"messages": [{"role": "user", "content": user_msg}]})
            log_llm_interaction(
                prompt_name=f"framework/deep_agent/{skill_name}",
                input_payload=user_msg[:500],
                output_payload={"total": collector.total_count},
                metadata={"batch": batch_idx, "size": len(batch)},
            )
        except Exception as ex:
            logger.error("[ENTITY:CANDIDATES] 배치 %d invoke 실패: %s", batch_idx, ex)


# ---------------------------------------------------------------------------
# 후보 추출
# ---------------------------------------------------------------------------


def _extract_entity_candidates(module_index: Dict[str, Any]) -> List[Dict[str, str]]:
    """module_index에서 엔티티 후보(CLASS/STRUCT/UNION)를 추출한다.

    DTO 디렉토리의 클래스는 제외하고, entity 디렉토리는 우선순위를 높인다.
    """
    candidates: List[Dict[str, str]] = []

    for fqn, info in module_index.items():
        if not isinstance(info, dict):
            continue
        if info.get("kind") not in _ENTITY_CANDIDATE_KINDS:
            continue

        fp = info.get("file_path", "")
        path_class = _classify_by_path(fp)

        # DTO 디렉토리는 제외
        if path_class == "dto":
            continue

        candidates.append({
            "fqn": fqn,
            "name": info.get("name", ""),
            "file_path": fp,
            "annotations": (info.get("annotations") or "")[:100],
            "comment": (info.get("comment") or "")[:100],
            "path_hint": path_class,
        })

    # entity 디렉토리 우선
    candidates.sort(key=lambda c: (0 if c.get("path_hint") == "entity" else 1))
    return candidates


def _classify_by_path(file_path: str) -> str:
    """파일 경로의 디렉토리명으로 entity/dto/unknown 사전 분류."""
    parts_lower = {p.lower() for p in Path(file_path).parts} if file_path else set()
    if parts_lower & _ENTITY_DIR_HINTS:
        return "entity"
    if parts_lower & _DTO_DIR_HINTS:
        return "dto"
    return "unknown"


# ---------------------------------------------------------------------------
# User Message 빌더
# ---------------------------------------------------------------------------


def _build_ddl_user_message(ddl_table_metadata: Dict) -> str:
    """DDL 시나리오용 user message를 구성한다."""
    parts: List[str] = []

    # 기존 FK 목록 (중복 방지)
    fk_lines: List[str] = []
    for (_schema, _name), meta in ddl_table_metadata.items():
        orig = meta.get("original_name", _name)
        for fk in (meta.get("foreign_keys") or meta.get("foreignKeys") or []):
            fk_col = fk.get("column", "")
            fk_ref = fk.get("ref", "")
            if fk_col and fk_ref:
                fk_lines.append(f"- {orig}.{fk_col} → {fk_ref}")

    if fk_lines:
        if len(fk_lines) <= 50:
            parts.append("[이미 식별된 FK — 중복 생성하지 마세요]\n" + "\n".join(fk_lines))
        else:
            parts.append(f"이미 식별된 FK {len(fk_lines)}개 존재. 중복 생성하지 마세요.")

    # 테이블 이름
    table_names = [meta.get("original_name", _name) for (_schema, _name), meta in ddl_table_metadata.items()]
    if len(table_names) <= 100:
        parts.append("[테이블 이름] " + ", ".join(table_names))
    else:
        parts.append(f"프로젝트에 테이블 {len(table_names)}개 존재. table_search_tool로 검색하세요.")

    parts.append(
        "위 테이블을 코드에서 사용하는 메서드를 추적하세요.\n"
        "이미 식별된 FK 이외의 새로운 FK 관계가 코드에서 발견되면 graph_create_fk_tool로 등록하세요."
    )

    return "\n".join(parts)


def _build_candidates_user_message(candidates: List[Dict[str, str]]) -> str:
    """후보 시나리오용 user message를 구성한다."""
    lines: List[str] = ["[클래스/구조체 후보]"]
    for c in candidates:
        line = f"- {c['fqn']} (file: {c['file_path']})"
        if c.get("annotations"):
            line += f" [annotations: {c['annotations']}]"
        if c.get("comment"):
            line += f" [comment: {c['comment']}]"
        lines.append(line)
    lines.append("\n위 후보에서 DB 엔티티를 식별하고, 사용하는 함수를 추적하세요.")
    return "\n".join(lines)


def _default_entity_prompt(has_ddl: bool) -> str:
    """SKILL.md가 없을 때의 폴백 엔티티 프롬프트."""
    try:
        from util.prompt_registry import prompts
        return prompts.deep_agent_entity_prompt(has_ddl=has_ddl)
    except Exception:
        return (
            "당신은 소스코드에서 DB 엔티티를 식별하고, "
            "각 함수가 어떤 테이블을 읽거나 쓰는지 추적하는 전문가 에이전트입니다."
        )
