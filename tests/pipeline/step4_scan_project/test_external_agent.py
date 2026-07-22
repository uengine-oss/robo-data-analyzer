from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path

from llm.usage.cost_budget import CostBudget
from pipeline.execution.pause_controller import PauseController
from agents.external import ExternalAgentRejected, run_external_agent
from pipeline.steps.step4_scan_project.external_skills import ExternalExtractionRule, ExternalSkill


def _skill(prefix: str = "CFG", name: str = "generic-config") -> ExternalSkill:
    return ExternalSkill(
        name=name,
        extensions=(".cfgx",),
        content_signatures=(f"{prefix}[",),
        rules=(ExternalExtractionRule(
            name="entry",
            pattern=rf"(?m)^{prefix}\[(?P<key>[A-Za-z][\w.]*)\]\s*=\s*(?P<value>.+)$",
            key_group="key",
            value_group="value",
        ),),
    )


class ExternalAgentTest(unittest.IsolatedAsyncioTestCase):
    async def _run(self, invoker, samples=None):
        with tempfile.TemporaryDirectory() as temp:
            return await run_external_agent(
                extension=".cfgx",
                samples=samples or ["CFG[shop.title] = 쇼핑몰\nALT[other.key] = v"],
                negative_samples=[],
                run_dir=Path(temp),
                cost_budget=CostBudget(limit_usd=10),
                llm_semaphore=asyncio.Semaphore(2),
                pause=PauseController(),
                proposal_invoker=invoker,
            )

    async def test_consensus_is_by_extracted_results_with_n7_agree4(self) -> None:
        async def invoke(candidate: int, attempt: int, feedback: str) -> ExternalSkill:
            return _skill("CFG", f"candidate-{candidate}") if candidate < 4 else _skill("ALT", f"candidate-{candidate}")

        selected = await self._run(invoke)
        self.assertTrue(selected.is_validated)
        self.assertTrue(selected.skill.name.startswith("candidate-"))
        self.assertIn("CFG", selected.skill.rules[0].pattern)

    async def test_soft_consensus_selects_largest_group_below_agree_threshold(self) -> None:
        # 합의는 선별 신호일 뿐 승인 근거가 아니다(agent-verifier-contract) —
        # 전원 결정론 검증을 통과했지만 결과셋이 3/4 로 갈려 4표 미달이어도
        # 최대 그룹 대표를 선별한다 (최종 승인은 호출부 held-out 게이트).
        async def invoke(candidate: int, attempt: int, feedback: str) -> ExternalSkill:
            return _skill("CFG", f"candidate-{candidate}") if candidate < 3 else _skill("ALT", f"candidate-{candidate}")

        selected = await self._run(invoke)
        self.assertTrue(selected.is_validated)
        self.assertIn("ALT", selected.skill.rules[0].pattern)   # 4표 그룹(ALT)이 최대

    async def test_skill_must_declare_the_learned_extension(self) -> None:
        # 배운 확장자를 선언하지 않은 skill 은 저장 후 다른 형식을 납치할 수 있다.
        async def invoke(candidate: int, attempt: int, feedback: str) -> ExternalSkill:
            skill = _skill("CFG", f"candidate-{candidate}")
            return skill.model_copy(update={"extensions": (".other",)})

        with self.assertRaises(ExternalAgentRejected):
            await self._run(invoke, ["CFG[shop.title] = 쇼핑몰"])

    async def test_all_candidates_invalid_is_still_rejected(self) -> None:
        async def invoke(candidate: int, attempt: int, feedback: str) -> ExternalSkill:
            return _skill("MISS", f"candidate-{candidate}")      # 어떤 sample 도 못 맞춤

        with self.assertRaises(ExternalAgentRejected):
            await self._run(invoke, ["CFG[shop.title] = 쇼핑몰"])

    async def test_one_repair_succeeds_and_never_exceeds_r2(self) -> None:
        calls: dict[int, int] = {}

        async def invoke(candidate: int, attempt: int, feedback: str) -> ExternalSkill:
            calls[candidate] = calls.get(candidate, 0) + 1
            self.assertLessEqual(attempt, 2)
            return _skill("MISS", f"candidate-{candidate}") if attempt == 0 else _skill("CFG", f"candidate-{candidate}")

        await self._run(invoke, ["CFG[shop.title] = 쇼핑몰"])
        self.assertEqual({2}, set(calls.values()))

    async def test_repeated_failure_terminates_early(self) -> None:
        calls = 0

        async def invoke(candidate: int, attempt: int, feedback: str) -> ExternalSkill:
            nonlocal calls
            calls += 1
            return _skill("MISS", f"candidate-{candidate}")

        with self.assertRaises(ExternalAgentRejected):
            await self._run(invoke, ["CFG[shop.title] = 쇼핑몰"])
        self.assertEqual(14, calls)

    async def test_external_cancellation_is_propagated_and_children_finish(self) -> None:
        started = asyncio.Event()
        live = 0

        async def invoke(candidate: int, attempt: int, feedback: str) -> ExternalSkill:
            nonlocal live
            live += 1
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                live -= 1

        task = asyncio.create_task(self._run(invoke))
        await started.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(0, live)


if __name__ == "__main__":
    unittest.main()
