from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path

from llm.usage.cost_budget import CostBudget
from pipeline.execution.pause_controller import PauseController
from agents.external import ExternalAgentRejected, _user_message, run_external_agent
from pipeline.steps.step4_scan_project.external_skills import (
    ExternalFormatProposal,
    ProposedExternalExtractionRule,
)


def _proposal(prefix: str = "CFG") -> ExternalFormatProposal:
    return ExternalFormatProposal(
        rules=(ProposedExternalExtractionRule(
            pattern=rf"(?m)^{prefix}\[(?P<key>[A-Za-z][\w.]*)\]\s*=\s*(?P<value>.+)$",
        ),),
    )


class ExternalAgentTest(unittest.IsolatedAsyncioTestCase):
    def test_initial_message_leaves_sample_facts_to_overview_tool(self) -> None:
        message = _user_message(".cfgx")

        self.assertIn("extension: .cfgx", message)
        self.assertIn("sample_overview", message)
        self.assertNotIn("training_sample_inventory", message)
        self.assertNotIn("total_chars", message)
        self.assertNotIn("total_lines", message)

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
        async def invoke() -> ExternalFormatProposal:
            return _proposal("CFG")

        selected = await self._run(invoke)
        self.assertTrue(selected.is_validated)
        self.assertEqual("external-cfgx-1", selected.skill.name)
        self.assertEqual("r1", selected.skill.rules[0].name)
        self.assertEqual("key", selected.skill.rules[0].key_group)
        self.assertEqual((".cfgx",), selected.skill.extensions)
        self.assertIn("CFG", selected.skill.rules[0].pattern)

    async def test_extension_is_analyzer_owned_not_model_output(self) -> None:
        async def invoke() -> ExternalFormatProposal:
            return _proposal("CFG")

        selected = await self._run(invoke, ["CFG[shop.title] = 쇼핑몰"])

        self.assertNotIn(
            "extensions", ExternalFormatProposal.model_json_schema()["properties"],
        )
        self.assertEqual({"rules"}, set(ExternalFormatProposal.model_json_schema()["properties"]))
        self.assertEqual((".cfgx",), selected.skill.extensions)

    async def test_all_candidates_invalid_is_still_rejected(self) -> None:
        async def invoke() -> ExternalFormatProposal:
            return _proposal("MISS")      # 어떤 sample 도 못 맞춤

        with self.assertRaises(ExternalAgentRejected):
            await self._run(invoke, ["CFG[shop.title] = 쇼핑몰"])

    async def test_validation_failure_is_not_repaired(self) -> None:
        calls = 0

        async def invoke() -> ExternalFormatProposal:
            nonlocal calls
            calls += 1
            return _proposal("MISS")

        with self.assertRaises(ExternalAgentRejected):
            await self._run(invoke, ["CFG[shop.title] = 쇼핑몰"])
        self.assertEqual(1, calls)

    async def test_external_cancellation_is_propagated_and_children_finish(self) -> None:
        started = asyncio.Event()
        live = 0

        async def invoke() -> ExternalFormatProposal:
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
