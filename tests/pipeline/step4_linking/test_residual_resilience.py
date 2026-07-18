from pathlib import Path
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

from pydantic import BaseModel

from llm.runners.structured_agent import (
    StructuredResponseMissingError,
    run_structured_agent,
)
from llm.usage.cost_budget import CostBudget
from pipeline.steps.step4_linking.link_tables.residual_pattern_loop import (
    run_residual_loop,
)


class _Result(BaseModel):
    value: str


def _case() -> dict:
    return {
        "case_id": "case-1",
        "file": "sample.c",
        "line": 1,
        "call_text": "SQL_CALL",
        "source_id": "code:sample.c:fn",
        "attempted_patterns": [],
    }


class ResidualResilienceTest(IsolatedAsyncioTestCase):
    async def test_structured_agent_raises_typed_error_when_submit_is_missing(self) -> None:
        agent = SimpleNamespace(ainvoke=AsyncMock(return_value={"messages": []}))
        with (
            patch("llm.runners.structured_agent.build_llm_client", return_value=object()),
            patch("llm.runners.structured_agent.build_standard_stack", return_value=[]),
            patch("llm.runners.structured_agent.create_agent", return_value=agent),
            patch("llm.runners.structured_agent.begin_agent_invoke"),
            patch("llm.runners.structured_agent.end_agent_invoke"),
        ):
            with self.assertRaises(StructuredResponseMissingError):
                await run_structured_agent(
                    agent_name="boundary",
                    invoke_id="run",
                    system_prompt="submit when done",
                    tools=[],
                    response_format=_Result,
                    user_message="inspect",
                    max_calls=1,
                    run_dir=Path("unused"),
                    cost_budget=CostBudget(limit_usd=1.0),
                )

    async def test_missing_submit_keeps_residual_without_failing_pipeline(self) -> None:
        case = _case()
        baseline = SimpleNamespace(edges=[], unresolved=[])
        with (
            patch(
                "pipeline.steps.step4_linking.link_tables.residual_pattern_loop.RegexTableLinker.link",
                new=AsyncMock(return_value=baseline),
            ),
            patch(
                "pipeline.steps.step4_linking.link_tables.residual_pattern_loop._find_pattern",
                new=AsyncMock(side_effect=StructuredResponseMissingError("missing")),
            ),
        ):
            outcome = await run_residual_loop(
                unresolved_cases=[case],
                scan_result={"confidence": 1.0, "evidence": "test"},
                project_root=Path("."),
                code_nodes=[],
                tables=[],
                run_dir=Path("."),
                cost_budget=CostBudget(limit_usd=1.0),
            )

        self.assertEqual(outcome.edges, [])
        self.assertEqual(outcome.residual, [case])
        self.assertEqual(outcome.patterns_found, 0)

    async def test_other_value_error_remains_fatal(self) -> None:
        baseline = SimpleNamespace(edges=[], unresolved=[])
        with (
            patch(
                "pipeline.steps.step4_linking.link_tables.residual_pattern_loop.RegexTableLinker.link",
                new=AsyncMock(return_value=baseline),
            ),
            patch(
                "pipeline.steps.step4_linking.link_tables.residual_pattern_loop._find_pattern",
                new=AsyncMock(side_effect=ValueError("schema invalid")),
            ),
        ):
            with self.assertRaisesRegex(ValueError, "schema invalid"):
                await run_residual_loop(
                    unresolved_cases=[_case()],
                    scan_result={"confidence": 1.0, "evidence": "test"},
                    project_root=Path("."),
                    code_nodes=[],
                    tables=[],
                    run_dir=Path("."),
                    cost_budget=CostBudget(limit_usd=1.0),
                )
