"""Bounded ExternalSkill proposal/repair/consensus over the shared LLM stack."""
from __future__ import annotations

import asyncio
from collections import defaultdict
import json
from pathlib import Path
from typing import Awaitable, Callable, Optional

from agents import AGENTS_PACKAGE_DIR
from agents.runtime import run_structured_agent
from llm.fanout import gather_with_warmup
from llm.usage.cost_budget import BudgetExceededError, CostBudget
from pipeline.execution.pause_controller import PauseController
from pipeline.steps.step4_scan_project.external_skills import (
    ExternalSkill,
    ExternalSkillValidationError,
    ValidatedExternalSkill,
    apply_validated_external_skill,
    validate_external_skill,
)
from shared.events.runtime_logger import log_error, log_info


CONSENSUS_N = 7
CONSENSUS_AGREE = 4
MAX_REPAIRS = 2
_SYSTEM_PROMPT_PATH = AGENTS_PACKAGE_DIR / "prompts" / "external.md"


class ExternalAgentRejected(RuntimeError):
    pass


ProposalInvoker = Callable[[int, int, str], Awaitable[ExternalSkill]]


def _user_message(extension: str, samples: list[str], feedback: str) -> str:
    bounded = [sample[:20000] for sample in samples[:3]]
    return "\n".join([
        f"extension: {extension}",
        f"validation_feedback: {feedback or '(first proposal)'}",
        "samples:",
        json.dumps(bounded, ensure_ascii=False, indent=2),
        "Infer one reusable ExternalSkill and call submit_external.",
    ])


def _result_signature(validated: ValidatedExternalSkill, samples: list[str]) -> tuple:
    return tuple(
        tuple(sorted((match.key, match.value, match.line) for match in apply_validated_external_skill(validated, sample)))
        for sample in samples
    )


async def run_external_agent(
    *,
    extension: str,
    samples: list[str],
    negative_samples: list[str],
    run_dir: Path,
    cost_budget: CostBudget,
    llm_semaphore: asyncio.Semaphore,
    pause: PauseController,
    stream_emit: Optional[Callable[[dict], None]] = None,
    proposal_invoker: ProposalInvoker | None = None,
) -> ValidatedExternalSkill:
    """Run n=7 proposals, each with at most two repairs, then result-set consensus."""
    if not samples:
        raise ExternalAgentRejected("external agent requires at least one text sample")

    async def default_invoke(candidate: int, attempt: int, feedback: str) -> ExternalSkill:
        return await run_structured_agent(
            agent_name="external",
            invoke_id=f"candidate-{candidate}-attempt-{attempt}",
            system_prompt=_SYSTEM_PROMPT_PATH.read_text(encoding="utf-8"),
            tools=[],
            response_format=ExternalSkill,
            user_message=_user_message(extension, samples, feedback),
            max_calls=6,
            run_dir=run_dir,
            cost_budget=cost_budget,
            stream_emit=stream_emit,
        )

    invoke = proposal_invoker or default_invoke

    async def candidate(candidate_index: int) -> ValidatedExternalSkill:
        feedback = ""
        previous_failure = ""
        for attempt in range(MAX_REPAIRS + 1):
            async with llm_semaphore:
                draft = await invoke(candidate_index, attempt, feedback)
            try:
                return validate_external_skill(
                    draft,
                    samples,
                    negative_samples=negative_samples,
                )
            except ExternalSkillValidationError as error:
                failure = str(error)
                if failure == previous_failure:
                    raise ExternalAgentRejected(
                        f"candidate {candidate_index} repeated validation failure: {failure}"
                    ) from error
                previous_failure = failure
                feedback = failure
        raise ExternalAgentRejected(f"candidate {candidate_index} exhausted repairs: {feedback}")

    async def consensus_operation() -> list:
        coroutines = [candidate(index) for index in range(CONSENSUS_N)]
        return await gather_with_warmup(coroutines, return_exceptions=True)

    results = await pause.run_restartable_during_pause(consensus_operation)
    accepted: list[ValidatedExternalSkill] = []
    for index, result in enumerate(results):
        if isinstance(result, asyncio.CancelledError):
            raise result
        if isinstance(result, BudgetExceededError):
            raise result
        if isinstance(result, BaseException):
            log_error("step4.external_agent", "candidate_rejected", result, candidate=index)
            continue
        accepted.append(result)
    groups: dict[tuple, list[ValidatedExternalSkill]] = defaultdict(list)
    for validated in accepted:
        groups[_result_signature(validated, samples)].append(validated)
    winners = [group for group in groups.values() if len(group) >= CONSENSUS_AGREE]
    if not winners:
        raise ExternalAgentRejected(
            f"external skill consensus failed: accepted={len(accepted)} groups={[len(group) for group in groups.values()]}"
        )
    winning_group = max(winners, key=lambda group: (len(group), tuple(_result_signature(group[0], samples))))
    selected = min(
        winning_group,
        key=lambda item: (sum(len(rule.pattern) for rule in item.skill.rules), item.fingerprint),
    )
    log_info(
        "step4.external_agent",
        "consensus",
        accepted=len(accepted),
        agreeing=len(winning_group),
        skill=selected.skill.name,
    )
    return selected
