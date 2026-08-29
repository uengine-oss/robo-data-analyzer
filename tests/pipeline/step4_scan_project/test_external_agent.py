from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path

from llm.usage.cost_budget import CostBudget
from pipeline.execution.pause_controller import PauseController
from agents.external import ExternalAgentRejected, run_external_agent
from pipeline.steps.step4_scan_project.external_skills import (
    ExternalExtractionRule,
    ExternalSkillProposal,
)


def _proposal(
    prefix: str = "CFG", name: str = "generic-config",
) -> ExternalSkillProposal:
    return ExternalSkillProposal(
        name=name,
        rules=(ExternalExtractionRule(
            name="entry",
            pattern=rf"(?m)^{prefix}\[(?P<key>[A-Za-z][\w.]*)\]\s*=\s*(?P<value>.+)$",
            key_group="key",
        ),),
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
        async def invoke() -> ExternalSkillProposal:
            return _proposal("CFG", "candidate")

        selected = await self._run(invoke)
        self.assertTrue(selected.is_validated)
        self.assertEqual("candidate", selected.skill.name)
        self.assertEqual((".cfgx",), selected.skill.extensions)
        self.assertIn("CFG", selected.skill.rules[0].pattern)

    async def test_extension_is_analyzer_owned_not_model_output(self) -> None:
        async def invoke() -> ExternalSkillProposal:
            return _proposal("CFG", "candidate")

        selected = await self._run(invoke, ["CFG[shop.title] = 쇼핑몰"])

        self.assertNotIn(
            "extensions", ExternalSkillProposal.model_json_schema()["properties"],
        )
        self.assertEqual((".cfgx",), selected.skill.extensions)

    async def test_all_candidates_invalid_is_still_rejected(self) -> None:
        async def invoke() -> ExternalSkillProposal:
            return _proposal("MISS", "candidate")      # 어떤 sample 도 못 맞춤

        with self.assertRaises(ExternalAgentRejected):
            await self._run(invoke, ["CFG[shop.title] = 쇼핑몰"])

    async def test_validation_failure_is_not_repaired(self) -> None:
        calls = 0

        async def invoke() -> ExternalSkillProposal:
            nonlocal calls
            calls += 1
            return _proposal("MISS", "candidate")

        with self.assertRaises(ExternalAgentRejected):
            await self._run(invoke, ["CFG[shop.title] = 쇼핑몰"])
        self.assertEqual(1, calls)

    async def test_external_cancellation_is_propagated_and_children_finish(self) -> None:
        started = asyncio.Event()
        live = 0

        async def invoke() -> ExternalSkillProposal:
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
