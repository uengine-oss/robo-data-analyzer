"""함수→변수 참조 탐색 (Framework 전용)."""

from __future__ import annotations

import logging
from typing import List, Tuple

from config.settings import settings
from util.prompt_executor import get_rule_loader

logger = logging.getLogger(__name__)


def run_deep_agent_variable_references(
    function_facts_list: list,
    variable_facts_list: list,
    api_key: str = "",
) -> List[Tuple[str, str]]:
    """함수→변수/상수 참조 관계를 LLM으로 탐색."""
    if not api_key or not function_facts_list or not variable_facts_list:
        return []

    from concurrent.futures import ThreadPoolExecutor, as_completed

    loader = get_rule_loader("deep_agent")

    var_entries = []
    for v in variable_facts_list:
        code = (getattr(v, "code", "") or "").strip()
        if code and ":" in code.split("\n")[0]:
            parts = code.split("\n")
            code = "\n".join(line.split(":", 1)[1].strip() if ":" in line else line for line in parts)
        entry = f"{v.variable_id} | {v.name} | {code}" if code else f"{v.variable_id} | {v.name}"
        var_entries.append(entry)

    if not var_entries:
        return []

    variables_text = "\n".join(var_entries)
    max_workers = max(1, settings.concurrency.max_concurrency)
    batch_size = settings.deep_agent.varref_batch_size
    all_refs: List[Tuple[str, str]] = []

    def _process_one(func) -> List[Tuple[str, str]]:
        code = (getattr(func, "code", "") or "").strip()
        if not code:
            return []
        try:
            result = loader.execute("variable_reference", {
                "code": code,
                "function_name": func.fqn or func.function_id,
                "variables": variables_text,
            }, api_key=api_key)
            referenced = result.get("referenced", []) if isinstance(result, dict) else []
            return [(func.function_id, vid) for vid in referenced if vid]
        except Exception as e:
            logger.debug("[DEEPAGENT:VAR] 변수 참조 탐색 실패 (%s): %s", func.function_id, e)
            return []

    for i in range(0, len(function_facts_list), batch_size):
        batch = function_facts_list[i:i + batch_size]
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {executor.submit(_process_one, f): f for f in batch}
            for future in as_completed(futures):
                refs = future.result()
                all_refs.extend(refs)

    logger.info("[DEEPAGENT:VAR] 변수 참조 탐색 완료 | functions=%d refs=%d", len(function_facts_list), len(all_refs))
    return all_refs
