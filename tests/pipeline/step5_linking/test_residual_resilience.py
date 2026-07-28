from pathlib import Path
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

from pydantic import BaseModel

from agents.runtime import (
    StructuredResponseMissingError,
    run_structured_agent,
)
from agents.middleware.call_limit import ModelCallLimitExceeded
from llm.usage.cost_budget import CostBudget
from agents.residual import (
    _remaining_original_cases,
    _samples_with_numbered_context,
    run_residual_loop,
)
from graph.schema.graph_edges import ReadsWrites


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
            patch("agents.runtime.build_llm_client", return_value=object()),
            patch("agents.runtime.build_standard_stack", return_value=[]),
            patch("agents.runtime.create_agent", return_value=agent),
            patch("agents.runtime.begin_agent_invoke"),
            patch("agents.runtime.end_agent_invoke"),
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
                "agents.residual.RegexTableLinker.link",
                new=AsyncMock(return_value=baseline),
            ),
            patch(
                "agents.residual._find_pattern",
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
                "agents.residual.RegexTableLinker.link",
                new=AsyncMock(return_value=baseline),
            ),
            patch(
                "agents.residual._find_pattern",
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

    async def test_call_limit_keeps_residual_and_does_not_kill_pipeline(self) -> None:
        case = _case()
        baseline = SimpleNamespace(edges=[], unresolved=[])
        with (
            patch(
                "agents.residual.RegexTableLinker.link",
                new=AsyncMock(return_value=baseline),
            ),
            patch(
                "agents.residual._find_pattern",
                new=AsyncMock(side_effect=ModelCallLimitExceeded("residual", 15)),
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

        self.assertEqual([case], outcome.residual)
        self.assertEqual([], outcome.edges)

    def test_residual_context_is_line_numbered_and_scoped(self) -> None:
        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "sample.c"
            source.write_text(
                "\n".join(f"line {i}" for i in range(1, 12)),
                encoding="utf-8",
            )
            case = {**_case(), "line": 6}
            got = _samples_with_numbered_context([case], root, radius=2)[0]

        self.assertEqual("absolute-file-line", got["source_coordinate_system"])
        self.assertIn("4 | line 4", got["numbered_source_context"])
        self.assertIn("6 | line 6", got["numbered_source_context"])
        self.assertIn("8 | line 8", got["numbered_source_context"])

    def test_reapplied_patterns_cannot_inflate_original_residual_population(self) -> None:
        case = {**_case(), "evidence_offset": 12}

        remaining = _remaining_original_cases(
            [case],
            baseline_resolved_sites=set(),
            applied_resolved_sites=set(),
            new_edges=[],
        )

        self.assertEqual([case], remaining)

    def test_only_same_physical_site_with_new_edge_resolves_original_case(self) -> None:
        case = {**_case(), "evidence_offset": 12}
        edge = ReadsWrites(
            source_id=case["source_id"],
            target_id="table:T",
            relation="READS",
            evidence_file=case["file"],
            evidence_line=case["line"],
        )

        remaining = _remaining_original_cases(
            [case],
            baseline_resolved_sites=set(),
            applied_resolved_sites={(case["file"], 12, case["source_id"])},
            new_edges=[edge],
        )

        self.assertEqual([], remaining)
