"""UnresolvedAgentLinker — unresolved 케이스마다 Linking Agent invoke.

invoke 별 quarantine (Plan §3.6):
- **각 case 마다 자기 자신의 ``ToolResultCollector``** — case 간 결과 안 섞임.
- ``set_graph_context()`` 는 ``contextvars`` 라 asyncio task 별 격리 → 병렬 안전.
- 같은 batch 안 케이스는 ``asyncio.gather`` + ``Semaphore`` 병렬.

batch_size 는 ``ROBO_PIPELINE_UNRESOLVED_BATCH_SIZE``.

회로 차단기 (``BudgetExceededError`` / ``ModelCallLimitExceeded``) 는 상위로 전파 —
silent skip 금지 (절대원칙 #3).
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from agent_tools.agent_output_models import LinkingAgentResult, UnresolvedCase
from agent_tools.neo4j_graph_tools import ToolResultCollector, set_graph_context
from analysis_flow.step4_linking.stage3_resolve_unresolved.linking_agent import run_linking_agent
from shared.config.env_catalog import ENV
from shared.llm.cost_budget import BudgetExceededError, CostBudget
from shared.llm.middleware.call_limit import ModelCallLimitExceeded
from shared.logging_and_stream.logger import log_error


@dataclass
class CaseInvokeOutcome:
    """case 1개 invoke 의 종합 결과.

    Agent 의 ``LinkingAgentResult`` + 그 invoke 안에서 도구가 누적한 collector snapshot.
    case 단위 quarantine → 결과가 다른 case 와 섞이지 X.
    """
    case_id: str
    function_id: str
    result: LinkingAgentResult
    snapshot: dict        # {"table_accesses": [...], "refer_tos": [...]}


class UnresolvedAgentLinker:
    """unresolved 케이스 배치 처리 — Linking Agent invoke."""

    async def link(
        self,
        unresolved_cases: list[dict],
        codebase_notes: str,
        run_dir: Path,
        cost_budget: CostBudget,
        neo_client,
        stream_emit: Optional[Callable[[dict], None]] = None,
    ) -> list[CaseInvokeOutcome]:
        """각 case → Linking Agent invoke (병렬 + case 별 quarantine)."""
        if not unresolved_cases:
            return []

        batch_size = ENV.pipeline.unresolved_batch_size
        semaphore = asyncio.Semaphore(ENV.pipeline.max_concurrency)
        outcomes: list[CaseInvokeOutcome] = []

        for i in range(0, len(unresolved_cases), batch_size):
            batch = unresolved_cases[i : i + batch_size]
            tasks = [
                self._invoke_one(
                    case_dict, codebase_notes, run_dir, cost_budget, neo_client,
                    stream_emit, semaphore,
                )
                for case_dict in batch
            ]
            batch_results = await asyncio.gather(*tasks, return_exceptions=True)
            for case_dict, r in zip(batch, batch_results):
                if isinstance(r, (BudgetExceededError, ModelCallLimitExceeded)):
                    raise r            # 회로 차단기 즉시 전파
                if isinstance(r, BaseException):
                    log_error(
                        "analysis_flow.step4_linking.stage3_resolve_unresolved.unresolved_agent_linker",
                        "invoke_case",
                        r,
                        case_id=case_dict.get("case_id"),
                    )
                    continue
                if isinstance(r, CaseInvokeOutcome):
                    outcomes.append(r)
        return outcomes

    async def _invoke_one(
        self,
        case_dict: dict,
        codebase_notes: str,
        run_dir: Path,
        cost_budget: CostBudget,
        neo_client,
        stream_emit,
        semaphore: asyncio.Semaphore,
    ) -> CaseInvokeOutcome:
        """case 1개 invoke — 자기 자신의 collector 로 quarantine.

        ``contextvars`` 는 asyncio task 별 자동 격리 — 병렬이어도 collector 안 섞임.
        """
        async with semaphore:
            case = UnresolvedCase.model_validate(case_dict)

            # case 별 quarantine — 자기 collector 주입
            collector = ToolResultCollector()
            set_graph_context(neo_client, collector)

            result = await run_linking_agent(
                case=case,
                codebase_notes=codebase_notes,
                run_dir=run_dir,
                cost_budget=cost_budget,
                stream_emit=stream_emit,
            )
            return CaseInvokeOutcome(
                case_id=case.case_id,
                function_id=case.function_id,
                result=result,
                snapshot=collector.snapshot_and_clear(),
            )
