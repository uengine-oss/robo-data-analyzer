"""미해결 참조 추적 드라이버 — Unresolved Reference Linker.

미매칭된 인덱스 키(SQL, Config, Entity, Bean 등)를 linking 에이전트로 처리해
코드 내 실제 사용처를 찾고 그래프 관계를 생성한다.

[설계 원칙]
  1. Progressive Disclosure: 시스템 프롬프트는 공통 지침만, 각 카테고리의 구체
     지침은 SKILL.md 에 기술. LLM 이 `list_skills` / `read_skill` 로 자율 로드.
  2. 배치 처리: 같은 카테고리의 아이템을 묶어서 에이전트 1회 호출로 처리.
  3. 에이전트 재사용: 공통 system prompt 를 쓰므로 에이전트는 전체 수행 1회 생성.
  4. 즉시 반영: 도구 호출 결과를 ToolResultCollector 에 수집,
     phase_linking 에서 배치 단위로 Neo4j 에 즉시 반영.

[처리 흐름]
  UnresolvedLink 목록
  → 카테고리별 그룹핑
  → 에이전트 1회 생성 (공통 system prompt)
  → 각 카테고리별 배치를 category 힌트와 함께 invoke
  → ToolResultCollector 에 결과 누적
  → Generator 로 yield → phase_linking 에서 Neo4j 반영
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, Generator, List, Optional, Tuple

from util.llm_audit_logger import log_llm_interaction
from analyzer.strategy.framework.common.models.node_models import (
    FkEdge,
    FunctionTableEdge,
    ReferToEdge,
    UnresolvedLink,
)
from analyzer.strategy.framework.linking.agent.linking_agent import init_linking_env
from analyzer.strategy.framework.linking.agent.linking_tools import ToolResultCollector

logger = logging.getLogger(__name__)


# 같은 카테고리 아이템을 묶는 배치 크기
_UNRESOLVED_BATCH_SIZE = 10


# 반환 타입 alias
_YieldType = Tuple[List[ReferToEdge], List[FunctionTableEdge], List[FkEdge]]


def run_unresolved_linking(
    unresolved_list: List[UnresolvedLink],
    state: Dict[str, Any],
    api_key: Optional[str] = None,
    run_dir: Optional[Path] = None,
    codebase_pattern: str = "",
) -> Generator[_YieldType, None, None]:
    """미해결 참조 목록을 linking 에이전트로 처리한다.

    처리 흐름:
      1. 에이전트 1회 생성 (공통 linking system prompt + 도구 + 스킬 로더)
      2. UnresolvedLink 를 카테고리별로 그룹핑
      3. 각 카테고리 그룹을 배치 단위로 invoke (user message 에 카테고리 힌트 + 프로파일 지침)
      4. 배치 완료마다 결과 yield (→ phase_linking 에서 Neo4j 즉시 반영)

    Args:
        codebase_pattern: scanning agent 가 뽑은 프레임워크·DB 접근 패턴 요약.
            user message 에 prose 섹션으로 주입되어 LLM 탐색의 길잡이로 쓰인다.

    Yields:
        (refer_to_edges, function_table_edges, fk_edges) — 배치 단위.
    """
    if not unresolved_list:
        logger.info("[DEEP_AGENT] unresolved 없음 → 즉시 종료")
        return

    # ── 1. 초기화 ──
    build_agent, _system_prompt, user_template = init_linking_env(
        state, api_key=api_key, run_dir=run_dir,
    )

    # ── 2. 카테고리별 그룹핑 ──
    groups = _group_by_category(unresolved_list)
    total = len(unresolved_list)
    logger.info(
        "[DEEP_AGENT] 시작 | total=%d categories=%s",
        total, {k: len(v) for k, v in groups.items()},
    )

    # ── 3. 에이전트 1회 생성 (공통 system prompt, 모든 카테고리 공용) ──
    collector = ToolResultCollector()
    agent = build_agent(collector)

    # ── 4. 카테고리별 처리 ──
    for category, items in groups.items():
        batches = [items[i:i + _UNRESOLVED_BATCH_SIZE] for i in range(0, len(items), _UNRESOLVED_BATCH_SIZE)]
        logger.info(
            "[DEEP_AGENT] category=%s | items=%d batches=%d",
            category, len(items), len(batches),
        )

        for batch_idx, batch in enumerate(batches):
            user_message = _build_batch_user_message(batch, category, user_template, codebase_pattern)
            invoke_ok = False
            try:
                agent.invoke({"messages": [{"role": "user", "content": user_message}]})
                log_llm_interaction(
                    prompt_name=f"framework/deep_agent/{category}",
                    input_payload=user_message,
                    output_payload={"total": collector.total_count},
                    metadata={"category": category, "batch": batch_idx, "items": len(batch)},
                )
                invoke_ok = True
            except Exception as ex:  # noqa: SILENT_FAIL — 한 배치 invoke 실패가 전체를 막지 않도록 errlog 후 다음 배치 진행
                logger.error("[DEEP_AGENT] invoke 실패 | category=%s batch=%d error=%s", category, batch_idx, ex)

            result = collector.snapshot_and_clear()
            if not invoke_ok:
                dropped = len(result["refer_to"]) + len(result["table_access"]) + len(result["fk"])
                if dropped:
                    logger.warning(
                        "[DEEP_AGENT] 실패 배치 부분결과 폐기 | category=%s batch=%d dropped=%d",
                        category, batch_idx, dropped,
                    )
                continue
            if result["refer_to"] or result["table_access"] or result["fk"]:
                logger.info(
                    "[DEEP_AGENT] 배치 완료 | category=%s batch=%d refer=%d table=%d fk=%d",
                    category, batch_idx, len(result["refer_to"]), len(result["table_access"]), len(result["fk"]),
                )
                yield (result["refer_to"], result["table_access"], result["fk"])

    logger.info("[DEEP_AGENT] 전체 완료 | total=%d", total)


def _group_by_category(items: List[UnresolvedLink]) -> Dict[str, List[UnresolvedLink]]:
    """UnresolvedLink를 ref_candidates의 category별로 그룹핑한다."""
    groups: Dict[str, List[UnresolvedLink]] = {}
    for u in items:
        info = u.ref_candidates[0] if u.ref_candidates else {}
        category = info.get("category", "other") if isinstance(info, dict) else "other"
        groups.setdefault(category, []).append(u)
    return groups


def _build_batch_user_message(
    batch: List[UnresolvedLink],
    category: str,
    user_template: str,
    codebase_pattern: str = "",
) -> str:
    """배치를 카테고리 힌트 + 프로파일 지침과 함께 user_template 에 채워 user message 를 생성."""
    data = [_serialize_unresolved(u) for u in batch]
    profile_hint = ""
    if codebase_pattern:
        profile_hint = (
            "\n---\n"
            "[프로젝트 프레임워크·DB 접근 패턴 분석 — 수색 지침]\n\n"
            f"{codebase_pattern}\n"
        )
    return user_template.format(
        category=category,
        data=json.dumps(data, ensure_ascii=False, default=str),
        profile_hint=profile_hint,
    )


def _serialize_unresolved(u: UnresolvedLink) -> Dict[str, Any]:
    """UnresolvedLink 를 LLM 입력용 dict 로 변환."""
    info = u.ref_candidates[0] if u.ref_candidates else {}
    base: Dict[str, Any] = {
        "function_id": u.function_id,
        "ref_candidates": u.ref_candidates,
    }
    # 미매칭 키 정보 평탄화 (LLM 가독성)
    if isinstance(info, dict) and not (u.function_id and u.function_id.strip()):
        base.update({
            "key": info.get("key", ""),
            "target_type": info.get("target_type", ""),
            "chunk_type": info.get("chunk_type", ""),
            "file_path": info.get("file_path", ""),
            "text_snippet": info.get("text_snippet", ""),
            "sql_type": info.get("sql_type", ""),
        })
    return base
