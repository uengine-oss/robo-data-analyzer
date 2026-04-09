"""DeepAgent 변수 참조 추적 — 변수 후보를 코드에서 탐색하여 함수와 연결.

엔티티 식별(deep_agent_entity.py)과 동일한 구조:
  변수 후보 배치 → DeepAgent(tool 기반) → bundle_search/get_chunk로 탐색 → REFERENCES 등록
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from config.settings import settings
from util.prompt_executor import get_rule_loader

from analyzer.strategy.framework.linking.agent.common import _init_deep_agent_env
from analyzer.strategy.framework.linking.agent.tools import build_tools

logger = logging.getLogger(__name__)


def _build_varref_user_message(variables: List[Dict[str, str]]) -> str:
    """변수 후보 목록을 DeepAgent user message로 변환."""
    lines = ["[변수/필드/상수 후보]"]
    for v in variables:
        line = f"- {v['variable_id']} | {v['name']}"
        if v.get("code"):
            line += f" | {v['code']}"
        if v.get("module"):
            line += f" (module: {v['module']})"
        lines.append(line)
    lines.append("\n위 변수들이 코드에서 실제로 사용되는 함수를 찾아 REFERENCES 관계를 등록하세요.")
    return "\n".join(lines)


def run_deep_agent_variable_references(
    function_facts_list: list,
    variable_facts_list: list,
    api_key: str = "",
    state: Optional[Dict[str, Any]] = None,
) -> List[Tuple[str, str]]:
    """DeepAgent로 변수→함수 참조 관계를 추적.

    Args:
        state: pipeline state (span_index, chunk_index 등). 없으면 폴백.

    Returns:
        [(function_id, variable_id), ...]
    """
    if not api_key or not function_facts_list or not variable_facts_list:
        return []

    # 변수 후보 구성
    var_candidates: List[Dict[str, str]] = []
    for v in variable_facts_list:
        code = (getattr(v, "code", "") or "").strip()
        if code and ":" in code.split("\n")[0]:
            parts = code.split("\n")
            code = "\n".join(line.split(":", 1)[1].strip() if ":" in line else line for line in parts)
        var_candidates.append({
            "variable_id": v.variable_id,
            "name": v.name,
            "code": code,
            "module": getattr(v, "module_key", "") or "",
        })

    if not var_candidates:
        return []

    if state is None:
        state = {}

    try:
        create_deep_agent, model = _init_deep_agent_env(state, api_key=api_key)
    except Exception as e:
        logger.error("[DEEPAGENT:VAR] 초기화 실패: %s", e)
        return []

    # REFERENCES 수집기
    ref_collector: List[Tuple[str, str]] = []
    collector_lock = threading.Lock()

    # 변수 참조용 tool 빌드 (graph_create_variable_ref_tool 포함)
    tools = build_tools(
        state, "", [], [],
        tool_scope="variable_reference",
        collector_lock=collector_lock,
        variable_ref_collector=ref_collector,
    )

    from util.prompt_registry import prompts
    system_prompt = prompts.deep_agent_varref_prompt()

    # 배치 처리
    da_cfg = settings.deep_agent
    batch_size = da_cfg.varref_batch_size
    max_workers = da_cfg.entity_max_workers  # 엔티티와 동일한 워커 설정 사용
    total = len(var_candidates)

    batches = [var_candidates[i:i + batch_size] for i in range(0, max(total, 1), batch_size)]
    logger.info(
        "[DEEPAGENT:VAR] 시작 | variables=%d batches=%d batch_size=%d workers=%d",
        total, len(batches), batch_size, max_workers,
    )

    from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError as FuturesTimeoutError

    BATCH_TIMEOUT = 300  # 배치당 최대 5분

    def _process_batch(batch_idx: int, batch: List[Dict]) -> int:
        batch_names = [v["name"] for v in batch[:5]]
        logger.info("[DEEPAGENT:VAR] 배치 %d 시작 | variables=%d (%s...)", batch_idx, len(batch), batch_names)
        before_count = len(ref_collector)
        batch_agent = create_deep_agent(
            model=model, tools=tools,
            system_prompt=system_prompt,
        )
        user_msg = _build_varref_user_message(batch)
        try:
            batch_agent.invoke({"messages": [{"role": "user", "content": user_msg}]})
        except Exception as ex:
            logger.error("[PHASE:2:DEEP_AGENT:VARREF] invoke 실패 batch=%d: %s", batch_idx, ex)
        added = len(ref_collector) - before_count
        logger.info("[DEEPAGENT:VAR] 배치 %d 완료 | REFERENCES +%d건", batch_idx, added)
        return added

    with ThreadPoolExecutor(max_workers=min(len(batches), max_workers)) as executor:
        futures = {
            executor.submit(_process_batch, idx, batch): idx
            for idx, batch in enumerate(batches)
        }
        for future in as_completed(futures):
            batch_idx = futures[future]
            try:
                future.result(timeout=BATCH_TIMEOUT)
            except TimeoutError:
                logger.error("[PHASE:2:DEEP_AGENT:VARREF] 배치 %d 타임아웃 (%ds) — 스킵", batch_idx, BATCH_TIMEOUT)
            except Exception as e:
                logger.error("[PHASE:2:DEEP_AGENT:VARREF] 배치 %d 처리 실패: %s", batch_idx, e)

    logger.info("[DEEPAGENT:VAR] 완료 | refs=%d", len(ref_collector))
    return ref_collector


