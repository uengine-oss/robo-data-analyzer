"""Actor 동의어 통합기 (1차: 모듈 내, 2차: 모듈 간 LLM).

원본 위치 (이전):
  - `analyzer/strategy/framework/pipeline/phase_summary.py` 의
    `_apply_module_unified_actors` + `_merge_cross_module_actors`.

흐름:
  1) 모듈 내 통합 (`apply_module_unified_actors`) — 결정론.
     module_summary 의 ``unified_actors`` 매핑(rep → [syn,...])을
     analysis_by_function 의 actor 에 일괄 적용.
  2) 모듈 간 통합 (`merge_cross_module_actors`) — LLM 1회 호출.
     ``rules/framework/actor_synonym_merge.yaml`` 로 동의어 그룹 도출.

호출자(phase_summary)는 본 모듈을 호출만 한다.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Set

from util.llm_rule_runner import prompts

logger = logging.getLogger(__name__)


def apply_module_unified_actors(
    analysis_by_function: Dict[str, Dict[str, Any]],
    summary_by_module: Dict[str, Dict[str, Any]],
    function_facts_list: List[Any],
) -> int:
    """1차: 모듈 내 unified_actors 적용 — 결정론.

    Returns:
        교체된 actor 개수.
    """
    module_to_functions: Dict[str, List[str]] = {}
    for m in function_facts_list or []:
        mk = getattr(m, "module_key", None)
        if mk:
            module_to_functions.setdefault(mk, []).append(m.function_id)

    replaced = 0
    for mk, mdata in summary_by_module.items():
        unified = mdata.get("unified_actors") or {}
        if not unified:
            continue
        syn_to_rep: Dict[str, str] = {}
        for rep, syns in unified.items():
            for syn in syns:
                syn_to_rep[syn] = rep

        for fid in module_to_functions.get(mk, []):
            data = analysis_by_function.get(fid)
            if not data:
                continue
            actor = (data.get("actor") or "").strip()
            if actor in syn_to_rep:
                data["actor"] = syn_to_rep[actor]
                replaced += 1
    return replaced


async def merge_cross_module_actors(
    analysis_by_function: Dict[str, Dict[str, Any]],
    summary_by_module: Dict[str, Dict[str, Any]],
    api_key: str,
    locale: str,
) -> Dict[str, str]:
    """2차: 모듈 간 동의어 통합 — LLM 1회 호출.

    LLM 결과 dict 가 아니면 fail-fast (이전 정책 — 폴백 없이 상위 전파).

    Returns:
        merge_map (syn → rep). 통합된 actor 가 없으면 빈 dict.
    """
    module_actors: Dict[str, Dict[str, Any]] = {}
    for fid, data in analysis_by_function.items():
        actor = (data.get("actor") or "").strip()
        if not actor:
            continue
        module_key = fid.rsplit(".", 1)[0] if "." in fid else fid
        bucket = module_actors.setdefault(module_key, {"actors": set(), "summary": ""})
        bucket["actors"].add(actor)

    for mk, mdata in summary_by_module.items():
        if mk in module_actors:
            module_actors[mk]["summary"] = mdata.get("summary", "")

    all_actors: Set[str] = set()
    for ma in module_actors.values():
        all_actors.update(ma["actors"])
    if len(all_actors) <= 1:
        return {}

    actor_module_list = _render_actor_module_list(module_actors, analysis_by_function)
    result = prompts.actor_synonym_merge(actor_module_list, locale, api_key)
    if not isinstance(result, dict):
        logger.error(
            "[ACTOR_MERGE] LLM 결과가 dict 아님 | type=%s",
            type(result).__name__,
        )
        raise RuntimeError(
            f"actor_synonym_merge LLM 결과가 dict 가 아님: {type(result).__name__}"
        )

    merge_map: Dict[str, str] = {}
    for rep, syns in result.items():
        if not isinstance(syns, list):
            continue
        for syn in syns:
            if syn != rep:
                merge_map[syn] = rep

    if merge_map:
        logger.info("[ACTOR_MERGE] 모듈 간 동의어 통합: %s", merge_map)
        for fid, data in analysis_by_function.items():
            actor = (data.get("actor") or "").strip()
            if actor in merge_map:
                data["actor"] = merge_map[actor]
    return merge_map


def _render_actor_module_list(
    module_actors: Dict[str, Dict[str, Any]],
    analysis_by_function: Dict[str, Dict[str, Any]],
) -> str:
    """LLM prompt 입력용 - 모듈별 actor + 함수 요약 목록 텍스트."""
    lines: List[str] = []
    for mk, ma in module_actors.items():
        module_name = mk.split(".")[-1] if "." in mk else mk
        module_summary = (ma["summary"] or "")[:150]
        for actor in sorted(ma["actors"]):
            func_summaries: List[str] = []
            for fid, data in analysis_by_function.items():
                if (data.get("actor") or "").strip() != actor:
                    continue
                func_name = fid.rsplit(".", 1)[-1] if "." in fid else fid
                func_summary = (data.get("summary") or "")[:80]
                func_summaries.append(f"    - {func_name}: {func_summary}")
            line = f"[{actor}] {module_name}: {module_summary}"
            if func_summaries:
                line += "\n" + "\n".join(func_summaries[:5])
            lines.append(line)
    return "\n\n".join(lines)


__all__ = ["apply_module_unified_actors", "merge_cross_module_actors"]
