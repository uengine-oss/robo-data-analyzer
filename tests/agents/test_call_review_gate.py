"""External-call review uses a deterministic fail-closed acceptance gate."""
from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

import agents.call_review as review_module
from agents.contracts import CallDropVerdict, ExternalCallReview


class ExternalCallReviewGateTest(unittest.IsolatedAsyncioTestCase):
    async def _review(self, drops, externals, defined=()):
        async def fake_run(**_kwargs):
            return ExternalCallReview(
                drops=[CallDropVerdict(callee=value, reason="test verdict") for value in drops]
            )

        with patch.object(review_module, "run_structured_agent", side_effect=fake_run):
            return await review_module.review_external_calls(
                externals, frozenset(defined), Path("."), cost_budget=object(),
            )

    async def test_confident_fake_is_dropped(self):
        got = await self._review(
            drops=["PRINT_ZAP_IN_T"],
            externals={"PRINT_ZAP_IN_T": 40, "real_ext_proc": 3},
        )
        self.assertEqual({"PRINT_ZAP_IN_T"}, got)

    async def test_defined_routine_is_untouchable(self):
        got = await self._review(
            drops=["my_func"], externals={"my_func": 2}, defined=["my_func"],
        )
        self.assertEqual(set(), got)

    async def test_unlisted_name_is_hallucination(self):
        got = await self._review(
            drops=["NEVER_LISTED"], externals={"real_ext": 1},
        )
        self.assertEqual(set(), got)

    async def test_drop_cap_rejects_everything(self):
        got = await self._review(
            drops=["a", "b", "c"],
            externals={"a": 1, "b": 1, "c": 1, "d": 1},
        )
        self.assertEqual(set(), got)

    async def test_empty_externals_never_invokes_agent(self):
        called = []

        async def fake_run(**_kwargs):
            called.append(1)

        with patch.object(review_module, "run_structured_agent", side_effect=fake_run):
            got = await review_module.review_external_calls(
                {}, frozenset(), Path("."), cost_budget=object(),
            )
        self.assertEqual(set(), got)
        self.assertEqual([], called)


if __name__ == "__main__":
    unittest.main()
