from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from agents.agent_runtime import AgentModelOutputError
from llm.usage.cost_budget import CostBudget
from pipeline.stages.scan.scan_codebase_step import scan_codebase_step


class ScanModelOutputBoundaryTest(unittest.IsolatedAsyncioTestCase):
    async def test_bad_agent_output_is_visible_unavailable_state_not_run_abort(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            run_dir = root / "run"
            run_dir.mkdir()
            config = {"configurable": {
                "codebase": SimpleNamespace(root=root),
                "run": SimpleNamespace(run_dir=run_dir),
                "cost_budget": CostBudget(limit_usd=1.0),
                "stream": None,
            }}
            with patch(
                "pipeline.stages.scan.scan_codebase_step.run_scan_agent",
                new=AsyncMock(side_effect=AgentModelOutputError("submit missing", kind="schema")),
            ):
                result = await scan_codebase_step(
                    {"code_nodes": [], "tables": []}, config
                )

        scan = result["scan_result"]
        self.assertEqual("unavailable", scan["analysis_status"])
        self.assertEqual([], scan["table_access_patterns"])
        self.assertIn("submit missing", scan["no_match_reason"])


if __name__ == "__main__":
    unittest.main()
