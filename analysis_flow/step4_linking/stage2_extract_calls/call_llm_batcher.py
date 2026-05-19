"""CallLLMBatcher — ``call_extract.yaml`` LLM 배치 호출 → ``Calls`` 엣지 + unresolved.

배치 크기 = ``ROBO_PIPELINE_CALL_LLM_BATCH_SIZE`` (기본 30).
같은 시스템 프롬프트 → RadixAttention 95%+ 적중.

scope 분류:
- ``internal`` / ``wrapper``  → ``Calls`` 엣지 (``callee_function_id`` 필요)
- ``external``                → ``Calls`` 엣지 (``scope="external"``)
- ``virtual_dispatch``        → ``unresolved_cases`` 큐 (Linking Agent stage3 가 추적)
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

from data_models.graph_edges import Calls
from shared.config.env_catalog import ENV
from shared.llm.cost_budget import CostBudget
from shared.llm.yaml_llm_runner import run_yaml_rule


class CallClassification(BaseModel):
    """Call 1개의 분류 결과."""
    call_id: str
    scope: Literal["internal", "external", "virtual_dispatch", "wrapper"]
    callee_function_id: Optional[str] = None       # internal / wrapper 만


class CallExtractResult(BaseModel):
    """``call_extract.yaml`` 의 응답 Pydantic — Linking 2 단계 결과."""
    classifications: list[CallClassification] = Field(default_factory=list)


class CallLLMBatcher:
    """Call site 들 → ``batch_size`` 단위 LLM 분류 → ``Calls`` 엣지 + unresolved 큐."""

    async def link(
        self,
        project: Any,
        call_sites: list[dict],
        codebase_notes: str,
        prompts_dir: Path,
        cost_budget: Optional[CostBudget] = None,
    ) -> tuple[list[Calls], list[dict]]:
        """call_sites 를 LLM 으로 분류 → (Calls 엣지, unresolved 케이스).

        Args:
            call_sites: ``[{call_id, caller_function_id, call_text, file, line}, ...]``
            codebase_notes: ``ProfileResult`` prefix (캐시 친화).
            prompts_dir: ``strategy.prompts_dir``.
            cost_budget: ``CostBudget``.

        Returns:
            ``(edges, unresolved_cases)`` — unresolved 는 virtual_dispatch / 분류 실패 케이스.
        """
        if not call_sites:
            return [], []

        batch_size = ENV.pipeline.call_llm_batch_size
        edges: list[Calls] = []
        unresolved: list[dict] = []

        for i in range(0, len(call_sites), batch_size):
            batch = call_sites[i : i + batch_size]
            result: CallExtractResult = await run_yaml_rule(
                prompts_dir=prompts_dir,
                rule_name="call_extract",
                user_values={
                    "codebase_notes": codebase_notes or "(없음)",
                    "calls_json": json.dumps(batch, ensure_ascii=False, indent=2),
                },
                response_model=CallExtractResult,
                budget=cost_budget,
            )

            call_lookup = {c["call_id"]: c for c in batch}
            classified_ids: set[str] = set()
            for cls in result.classifications:
                call = call_lookup.get(cls.call_id)
                if call is None:
                    continue
                classified_ids.add(cls.call_id)

                if cls.scope in ("internal", "wrapper") and cls.callee_function_id:
                    edges.append(Calls(
                        caller_function_id=call["caller_function_id"],
                        callee_function_id=cls.callee_function_id,
                        scope="internal",
                    ))
                elif cls.scope == "external" and cls.callee_function_id:
                    edges.append(Calls(
                        caller_function_id=call["caller_function_id"],
                        callee_function_id=cls.callee_function_id,
                        scope="external",
                    ))
                elif cls.scope == "virtual_dispatch":
                    # Linking Agent 3 단계가 추적 — unresolved 큐로
                    unresolved.append(_to_unresolved_case(call, diagnosis="virtual_dispatch"))
                elif cls.callee_function_id is None:
                    unresolved.append(_to_unresolved_case(call, diagnosis="missing_callee"))

            # LLM 응답에 빠진 call (분류 누락) — unresolved 로
            for call_id, call in call_lookup.items():
                if call_id not in classified_ids:
                    unresolved.append(_to_unresolved_case(call, diagnosis="unclassified"))

        return edges, unresolved


def _to_unresolved_case(call: dict, diagnosis: str) -> dict:
    """call site → ``UnresolvedCase`` dict (Linking Agent 가 ``model_validate`` 함)."""
    return {
        "case_id": f"call:{call['call_id']}",
        "file": call.get("file", ""),
        "line": call.get("line", 0),
        "call_text": call.get("call_text", ""),
        "function_id": call.get("caller_function_id", ""),
        "attempted_patterns": [],
    }
