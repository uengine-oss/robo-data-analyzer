"""Bounded ExternalSkill proposal/repair/consensus over the shared LLM stack."""
from __future__ import annotations

import asyncio
from collections import defaultdict
import json
from pathlib import Path
from typing import Awaitable, Callable, Optional

from agents import AGENTS_PACKAGE_DIR
from agents.runtime import run_structured_agent
from agents.tools.mining import make_structure_candidate_tool
from agents.tools.sampling import make_sample_exploration_tools
from agents.tools.validation import make_verify_external_rule_tool
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


_PREVIEW_CHARS = 4000


def _user_message(extension: str, samples: list[str], feedback: str) -> str:
    """앞부분 미리보기 + 전체 크기 메타 — 전문 열람은 탐색 도구가 담당한다."""
    previews = []
    for index, sample in enumerate(samples[:3]):
        previews.append({
            "file_index": index,
            "total_chars": len(sample),
            "preview_is_partial": len(sample) > _PREVIEW_CHARS,
            "preview": sample[:_PREVIEW_CHARS],
        })
    return "\n".join([
        f"extension: {extension}",
        f"validation_feedback: {feedback or '(first proposal)'}",
        "samples (앞부분 미리보기 — preview_is_partial=true 면 전체는 도구로 열람):",
        json.dumps(previews, ensure_ascii=False, indent=2),
        "형식을 관찰·검증한 뒤 ExternalSkill 을 submit_external 로 제출하라.",
    ])


def _result_signature(validated: ValidatedExternalSkill, samples: list[str]) -> tuple:
    return tuple(
        tuple(sorted((match.key, match.start, match.end) for match in apply_validated_external_skill(validated, sample)))
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
    initial_feedback: str = "",
) -> ValidatedExternalSkill:
    """Run n=7 proposals, each with at most two repairs, then result-set consensus.

    ``initial_feedback`` 는 호출부(held-out 게이트)의 이전 실패 사유 — 재제안 시
    모든 후보의 첫 프롬프트에 주입된다.
    """
    if not samples:
        raise ExternalAgentRejected("external agent requires at least one text sample")

    async def default_invoke(candidate: int, attempt: int, feedback: str) -> ExternalSkill:
        # 도구는 전부 "파일 전문" 기준 — 미리보기는 앞부분일 뿐이며, 큰 파일과
        # 뒤에서 구조가 바뀌는 파일은 탐색 도구로 확인한다. verify 는 production
        # 엔진 그대로라 "에이전트가 검증한 것 == 검증기가 실행할 것" (Scan 동형).
        full_samples = samples[:3]
        tools = [
            make_structure_candidate_tool(full_samples),
            *make_sample_exploration_tools(full_samples),
            make_verify_external_rule_tool(full_samples, negative_samples[:5]),
        ]
        return await run_structured_agent(
            agent_name="external",
            invoke_id=f"candidate-{candidate}-attempt-{attempt}",
            system_prompt=_SYSTEM_PROMPT_PATH.read_text(encoding="utf-8"),
            tools=tools,
            response_format=ExternalSkill,
            user_message=_user_message(extension, samples, feedback),
            max_calls=16,
            run_dir=run_dir,
            cost_budget=cost_budget,
            stream_emit=stream_emit,
        )

    invoke = proposal_invoker or default_invoke

    async def candidate(candidate_index: int) -> ValidatedExternalSkill:
        feedback = initial_feedback
        previous_failure = ""
        for attempt in range(MAX_REPAIRS + 1):
            async with llm_semaphore:
                draft = await invoke(candidate_index, attempt, feedback)
            try:
                if extension and extension not in draft.extensions:
                    # 배운 확장자를 선언하지 않은 skill 은 저장 후 다른 형식을 납치하거나
                    # 정작 자기 형식에 재사용되지 못한다 — 결정론으로 즉시 반려.
                    # (확장자 없는 그룹은 저장 자체를 안 하므로 검사 대상이 아니다.)
                    raise ExternalSkillValidationError(
                        f"skill extensions {list(draft.extensions)} must include the learned extension {extension}"
                    )
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
    if not accepted:
        raise ExternalAgentRejected(
            f"external skill proposals all failed deterministic validation (candidates={CONSENSUS_N})"
        )
    # 합의는 후보 "선별" 신호다 — 승인 근거가 아니다 (agent-verifier-contract).
    # accepted 는 이미 전원 결정론 검증(samples+negative)을 통과했고, 최종 승인은
    # 호출부의 held-out 게이트가 한다. 따라서 ≥CONSENSUS_AGREE 동률 그룹이 없어도
    # 기각하지 않고 최대 그룹 대표를 선별한다 (공백 처리 등 미세 차이로 결과셋이
    # 갈리는 약한 로컬 모델에서 유효한 제안을 버리지 않기 위함 — 실측 근거).
    groups: dict[tuple, list[ValidatedExternalSkill]] = defaultdict(list)
    for index, validated in enumerate(accepted):
        try:
            signature = _result_signature(validated, samples)
        except ExternalSkillValidationError as error:
            # 검증은 통과했지만 재적용(서명 계산)에서 죽는 후보(wall-clock 변동 등)는
            # run 크래시가 아니라 후보 탈락으로 처리한다 (fail-closed).
            log_error("step4.external_agent", "candidate_rejected", error, candidate=index)
            continue
        groups[signature].append(validated)
    if not groups:
        raise ExternalAgentRejected(
            "all validated candidates failed signature re-application"
        )
    winning_group = max(
        groups.values(),
        key=lambda group: (len(group), tuple(_result_signature(group[0], samples))),
    )
    selected = min(
        winning_group,
        key=lambda item: (sum(len(rule.pattern) for rule in item.skill.rules), item.fingerprint),
    )
    log_info(
        "step4.external_agent",
        "consensus" if len(winning_group) >= CONSENSUS_AGREE else "consensus_soft",
        accepted=len(accepted),
        agreeing=len(winning_group),
        groups=sorted((len(group) for group in groups.values()), reverse=True),
        skill=selected.skill.name,
    )
    return selected
