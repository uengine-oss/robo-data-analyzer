"""DeepAgent: 미매칭 인덱스 키 추적 + 엔티티 식별 통합."""

from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path
from typing import Any, Dict, Generator, List, Optional, Tuple

from config.settings import settings
from util.artifact_logger import log_llm_interaction
from analyzer.strategy.framework.common.models import (
    FunctionTableEdge,
    ReferToEdge,
    UnresolvedLink,
)
from analyzer.strategy.framework.common.models import FkEdge

from analyzer.strategy.framework.linking.agent.common import _init_deep_agent_env
from analyzer.strategy.framework.linking.agent.tools import (
    build_artifact_lookup_index,
    build_tools,
    config_lookup,
    sql_lookup,
)

from analyzer.strategy.framework.linking.agent.entity import run_deep_agent_entity_identification
from analyzer.strategy.framework.linking.agent.varref import run_deep_agent_variable_references

logger = logging.getLogger(__name__)


def _system_prompt_text() -> str:
    from util.prompt_registry import prompts
    return prompts.deep_agent_system_prompt()


def _build_unmatched_user_message(u: UnresolvedLink) -> str:
    """미매칭 인덱스 키에 대한 종류별 user message 생성."""
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
            f"위 원문에서 클래스명, 인터페이스명, 메서드명 등 식별자를 먼저 파악한 뒤,\n"
            f"이 청크를 사용하는 메서드를 찾으십시오.\n"
            f"1) 원문에 등장하는 식별자로 bundle_search → 소스코드 사용처 추적\n"
            f"2) 발견된 각 메서드의 사용 패턴 판별 (CRUD, 호출, 참조 등)\n"
            f"3) graph_create_refer_to_tool 생성 (source_function_id 필수)\n"
            f"4) 테이블 접근이 확인되면 graph_create_table_access_tool도 생성\n"
        )
    elif category == "sql":
        header += (
            f"sql_type: {info.get('sql_type', '')}\n"
            f"file_path: {info.get('file_path', '')}\n\n"
            f"이 SQL을 실행하는 메서드를 찾으십시오.\n"
            f"1) sql_key '{key}' 또는 파일명/네임스페이스로 bundle_search\n"
            f"2) 발견된 메서드에 대해 graph_create_refer_to_tool 생성 (source_function_id 필수)\n"
        )
    elif category == "config":
        header += (
            f"file_path: {info.get('file_path', '')}\n\n"
            f"이 설정을 참조하는 메서드를 찾으십시오.\n"
            f"1) 설정 키 '{key}' 또는 파일명으로 bundle_search\n"
            f"2) 발견된 메서드에 대해 graph_create_refer_to_tool 생성 (source_function_id 필수)\n"
        )
    else:
        header += f"\ninfo: {json.dumps(info, ensure_ascii=False, default=str)}\n"

    return header


def run_deep_agent(
    unresolved_list: List[UnresolvedLink],
    state: Dict[str, Any],
    api_key: Optional[str] = None,
    run_dir: Optional[Path] = None,
) -> Generator[Tuple[List[ReferToEdge], List[FunctionTableEdge], List[FkEdge]], None, None]:
    """unresolved 목록을 DeepAgent 로 처리. 항목별로 (refer_to, function_table, fk_edges) yield."""
    logger.info("[PHASE:2:DEEP_AGENT] 진입 | unresolved=%d api_key_set=%s run_dir=%s", len(unresolved_list), bool(api_key), run_dir)
    if not unresolved_list:
        logger.info("[PHASE:2:DEEP_AGENT] unresolved 없음 → 즉시 종료")
        return

    verbose = os.environ.get("FW_DEEPAGENT_VERBOSE", "true").strip().lower() == "true"

    try:
        create_deep_agent, model = _init_deep_agent_env(state, api_key=api_key, run_dir=run_dir)
    except Exception as e:
        logger.error("[PHASE:2:DEEP_AGENT] deepagents 미설치 또는 LLM 클라이언트 없음: %s", e)
        return

    system_prompt = _system_prompt_text()
    total = len(unresolved_list)
    max_workers = min(total, settings.concurrency.max_concurrency)

    def _process_one(idx: int, u: UnresolvedLink) -> Tuple[List[ReferToEdge], List[FunctionTableEdge], List[FkEdge]]:
        has_method = bool(u.function_id and u.function_id.strip())
        label = u.function_id if has_method else "(unmatched key)"
        logger.info("[PHASE:2:DEEP_AGENT] LLM 처리 (%d/%d) %s", idx, total, label)

        if verbose and u.ref_candidates:
            parts: List[str] = []
            for c in (u.ref_candidates or [])[:5]:
                if isinstance(c, dict):
                    parts.append(f"{c.get('target_type', c.get('kind', '?'))}={c.get('key', c.get('value', ''))}")
                else:
                    parts.append(f"{getattr(c, 'kind', '?')}={getattr(c, 'value', '')}")
            logger.info("[PHASE:2:DEEP_AGENT] 후보 요약 | %s candidates=%s", label, "; ".join(parts))

        refer_collector: List[ReferToEdge] = []
        table_collector: List[FunctionTableEdge] = []
        fk_collector: List[FkEdge] = []
        collector_lock = threading.Lock()
        tools = build_tools(
            state, u.function_id or "", refer_collector, table_collector,
            variable_id=u.variable_id, fk_collector=fk_collector,
            tool_scope="full",
            collector_lock=collector_lock,
        )

        agent = create_deep_agent(model=model, tools=tools, system_prompt=system_prompt)

        if has_method:
            user_content = f"function_id: {u.function_id}\nref_candidates: {json.dumps(u.ref_candidates, ensure_ascii=False, default=str)}"
        else:
            user_content = _build_unmatched_user_message(u)

        try:
            agent.invoke({"messages": [{"role": "user", "content": user_content}]})
            log_llm_interaction(
                prompt_name="framework/deep_agent",
                input_payload=user_content,
                output_payload={
                    "refer_to": len(refer_collector),
                    "table_access": len(table_collector),
                    "fk_edges": len(fk_collector),
                },
                metadata={"function_id": u.function_id or "", "idx": idx, "total": total},
            )
            if verbose and (refer_collector or table_collector or fk_collector):
                logger.info(
                    "[PHASE:2:DEEP_AGENT] 결과 | %s new_refer=%d new_mt=%d new_fk=%d",
                    label, len(refer_collector), len(table_collector), len(fk_collector),
                )
        except Exception as ex:
            logger.error("[PHASE:2:DEEP_AGENT] invoke 실패 %s error=%s", label, ex)

        return refer_collector, table_collector, fk_collector

    total_refer = 0
    total_mt = 0
    total_fk = 0
    logger.info("[PHASE:2:DEEP_AGENT] 병렬 처리 시작 | items=%d workers=%d", total, max_workers)

    from concurrent.futures import ThreadPoolExecutor, as_completed
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(_process_one, idx, u): idx
            for idx, u in enumerate(unresolved_list, start=1)
        }
        for future in as_completed(futures):
            try:
                refer_result, mt_result, fk_result = future.result()
                if refer_result or mt_result or fk_result:
                    total_refer += len(refer_result)
                    total_mt += len(mt_result)
                    total_fk += len(fk_result)
                    yield (refer_result, mt_result, fk_result)
            except Exception as ex:
                logger.error("[PHASE:2:DEEP_AGENT] 워커 예외: %s", ex)

    logger.info("[PHASE:2:DEEP_AGENT] 종료 | new_refer=%d new_mt=%d new_fk=%d", total_refer, total_mt, total_fk)


__all__ = [
    "run_deep_agent",
    "run_deep_agent_entity_identification",
    "run_deep_agent_variable_references",
    "build_tools",
    "sql_lookup",
    "config_lookup",
    "build_artifact_lookup_index",
]
