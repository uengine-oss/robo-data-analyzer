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
        explanation="generic bracketed key/value records",
        provenance="test sample structure",
    )


class ExternalAgentTest(unittest.IsolatedAsyncioTestCase):
    async def _run(self, invoker, samples=None):
        with tempfile.TemporaryDirectory() as temp:
            return await run_external_agent(
                extension=".cfgx",
                # The default accepted fixture has one declared record grammar and
                # every non-whitespace source span belongs to it.  Mixed CFG/ALT
                # input is a partial-parser rejection contract, not a valid proposal.
                samples=samples or ["CFG[shop.title] = 쇼핑몰\nCFG[other.key] = v"],
                negative_samples=[],
                run_dir=Path(temp),
                cost_budget=CostBudget(limit_usd=10),
                llm_semaphore=asyncio.Semaphore(2),
                pause=PauseController(),
                proposal_invoker=invoker,
            )

    async def test_single_valid_proposal_is_accepted(self) -> None:
        async def invoke() -> ExternalSkill:
            return _skill("CFG", "candidate")

        selected = await self._run(invoke)
        self.assertTrue(selected.is_validated)
        self.assertEqual("candidate", selected.skill.name)
        self.assertIn("CFG", selected.skill.rules[0].pattern)

    async def test_skill_must_declare_the_learned_extension(self) -> None:
        # 배운 확장자를 선언하지 않은 skill 은 저장 후 다른 형식을 납치할 수 있다.
        async def invoke() -> ExternalSkill:
            skill = _skill("CFG", "candidate")
            return skill.model_copy(update={"extensions": (".other",)})

        with self.assertRaises(ExternalAgentRejected):
            await self._run(invoke, ["CFG[shop.title] = 쇼핑몰"])

    async def test_all_candidates_invalid_is_still_rejected(self) -> None:
        async def invoke() -> ExternalSkill:
            return _skill("MISS", "candidate")      # 어떤 sample 도 못 맞춤

        with self.assertRaises(ExternalAgentRejected):
            await self._run(invoke, ["CFG[shop.title] = 쇼핑몰"])

    async def test_validation_failure_is_not_repaired(self) -> None:
        calls = 0

        async def invoke() -> ExternalSkill:
            nonlocal calls
            calls += 1
            return _skill("MISS", "candidate")

        with self.assertRaises(ExternalAgentRejected):
            await self._run(invoke, ["CFG[shop.title] = 쇼핑몰"])
        self.assertEqual(1, calls)

    async def test_external_cancellation_is_propagated_and_children_finish(self) -> None:
        started = asyncio.Event()
        live = 0

        async def invoke() -> ExternalSkill:
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
