"""Single structured ExternalSkill proposal with deterministic validation."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Awaitable, Callable, Optional

from agents import AGENTS_PACKAGE_DIR
from agents.runtime import run_structured_agent
from agents.tools.mining import make_structure_candidate_tool
from agents.tools.sampling import make_sample_exploration_tools
from agents.tools.validation import make_verify_external_rule_tool
from llm.usage.cost_budget import CostBudget
from pipeline.execution.pause_controller import PauseController
from pipeline.steps.step4_scan_project.external_skills import (
    ExternalSkill,
    ExternalSkillValidationError,
    ValidatedExternalSkill,
    validate_external_skill,
)
from shared.events.runtime_logger import log_info
_SYSTEM_PROMPT_PATH = AGENTS_PACKAGE_DIR / "prompts" / "external.md"


class ExternalAgentRejected(RuntimeError):
    pass


ProposalInvoker = Callable[[], Awaitable[ExternalSkill]]


_PREVIEW_CHARS = 4000


def _user_message(extension: str, samples: list[str]) -> str:
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
        "samples (앞부분 미리보기 — preview_is_partial=true 면 전체는 도구로 열람):",
        json.dumps(previews, ensure_ascii=False, indent=2),
        "형식을 관찰·검증한 뒤 ExternalSkill 을 submit_external 로 제출하라.",
    ])


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
    """Request one proposal and accept it only after deterministic validation."""
    if not samples:
        raise ExternalAgentRejected("external agent requires at least one text sample")

    async def default_invoke() -> ExternalSkill:
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
            invoke_id="proposal",
            system_prompt=_SYSTEM_PROMPT_PATH.read_text(encoding="utf-8"),
            tools=tools,
            response_format=ExternalSkill,
            user_message=_user_message(extension, samples),
            max_calls=16,
            run_dir=run_dir,
            cost_budget=cost_budget,
            stream_emit=stream_emit,
        )

    invoke = proposal_invoker or default_invoke

    async def proposal_operation() -> ValidatedExternalSkill:
        async with llm_semaphore:
            draft = await invoke()
        if extension and extension not in draft.extensions:
            raise ExternalAgentRejected(
                f"skill extensions {list(draft.extensions)} must include "
                f"the learned extension {extension}"
            )
        try:
            return validate_external_skill(
                draft, samples, negative_samples=negative_samples
            )
        except ExternalSkillValidationError as error:
            raise ExternalAgentRejected(str(error)) from error

    selected = await pause.run_restartable_during_pause(proposal_operation)
    log_info(
        "step4.external_agent",
        "proposal_validated",
        skill=selected.skill.name,
    )
    return selected
