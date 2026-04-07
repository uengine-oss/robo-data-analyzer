"""
Actor 동의어 통합 모듈.

2단계로 Actor 동의어를 제거합니다:
1차: module_summary에서 모듈 내부 동의어 통합 (프롬프트 unified_actors 출력)
2차: 이 모듈에서 모듈 간 동의어 통합 (LLM 1회 호출)

Actor 노드는 2차 통합 완료 후 한 번에 생성합니다.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, List

from langchain_core.messages import HumanMessage, SystemMessage

logger = logging.getLogger(__name__)


def apply_module_unified_actors(
    summary_by_function: Dict[str, Dict[str, Any]],
    summary_by_module: Dict[str, Dict[str, Any]],
    function_facts_list: list,
) -> int:
    """1차 통합 결과(module_summary의 unified_actors)를 summary_by_function에 적용.

    Returns: 교체된 actor 수
    """
    # module_key → function_id 매핑
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
        # {대표명: [동의어들]} → {동의어: 대표명} 역매핑
        syn_to_rep = {}
        for rep, syns in unified.items():
            for syn in syns:
                syn_to_rep[syn] = rep

        fids = module_to_functions.get(mk, [])
        for fid in fids:
            data = summary_by_function.get(fid)
            if not data:
                continue
            actor = (data.get("actor") or "").strip()
            if actor in syn_to_rep:
                data["actor"] = syn_to_rep[actor]
                replaced += 1

    return replaced


async def merge_cross_module_actors(
    summary_by_function: Dict[str, Dict[str, Any]],
    summary_by_module: Dict[str, Dict[str, Any]],
    api_key: str,
    locale: str,
) -> Dict[str, str]:
    """2차: 모듈 간 동의어 통합. LLM 1회 호출.

    각 모듈의 대표 actor + 모듈 summary를 전달하여 동의어를 판단합니다.

    Returns: {동의어: 대표명} 매핑. 빈 dict이면 통합 없음.
    """
    # 모듈별 actor 수집
    module_actors: Dict[str, Dict[str, Any]] = {}
    for fid, data in summary_by_function.items():
        actor = (data.get("actor") or "").strip()
        if not actor:
            continue
        # function_id에서 module_key 추출 (마지막 . 앞)
        module_key = fid.rsplit(".", 1)[0] if "." in fid else fid
        if module_key not in module_actors:
            module_actors[module_key] = {"actors": set(), "summary": ""}
        module_actors[module_key]["actors"].add(actor)

    # 모듈 summary 추가
    for mk, mdata in summary_by_module.items():
        if mk in module_actors:
            module_actors[mk]["summary"] = mdata.get("summary", "")

    # 전체 actor 수
    all_actors = set()
    for ma in module_actors.values():
        all_actors.update(ma["actors"])
    if len(all_actors) <= 1:
        return {}

    # LLM 프롬프트 구성
    prompt = (
        "다음은 각 모듈의 Actor와 모듈 요약입니다.\n"
        "같은 역할을 의미하는 Actor를 통합하세요.\n"
        "판단 기준: 같은 사람/시스템이 수행하는 행위이면 동의어입니다.\n\n"
    )
    for mk, ma in module_actors.items():
        module_name = mk.split(".")[-1] if "." in mk else mk
        for actor in sorted(ma["actors"]):
            summary_text = (ma["summary"] or "")[:100]
            prompt += f"[{actor}] {module_name}: {summary_text}\n"
    prompt += '\n출력 형식 (JSON만): {"대표명": ["동의어1", "동의어2"], ...}\n'
    prompt += "동의어가 없으면 {} 출력"

    try:
        from langchain_openai import ChatOpenAI
        llm = ChatOpenAI(
            model=os.getenv("LLM_MODEL", "gpt-4o-mini"),
            api_key=api_key,
            temperature=0,
        )
        response = llm.invoke([
            SystemMessage(content="Actor 동의어를 통합하는 전문가입니다. JSON만 출력하세요."),
            HumanMessage(content=prompt),
        ])
        text = response.content if hasattr(response, "content") else str(response)
        text = text.strip()
        if text.startswith("```"):
            first_nl = text.find("\n")
            if first_nl != -1:
                text = text[first_nl + 1:]
            if text.endswith("```"):
                text = text[:-3]
            text = text.strip()
        groups = json.loads(text)
    except Exception as e:
        logger.warning("[ACTOR_MERGE] LLM 호출/파싱 실패: %s", e)
        return {}

    # {대표명: [동의어들]} → {동의어: 대표명} 역매핑
    merge_map: Dict[str, str] = {}
    if isinstance(groups, dict):
        for rep, syns in groups.items():
            if isinstance(syns, list):
                for syn in syns:
                    if syn != rep:
                        merge_map[syn] = rep

    if merge_map:
        logger.info("[ACTOR_MERGE] 모듈 간 동의어 통합: %s", merge_map)
        # summary_by_function의 actor 교체
        for fid, data in summary_by_function.items():
            actor = (data.get("actor") or "").strip()
            if actor in merge_map:
                data["actor"] = merge_map[actor]

    return merge_map
