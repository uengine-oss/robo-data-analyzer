import json
from pathlib import Path
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

from llm.usage.cost_budget import CostBudget
from agents.residual import (
    ResidualPatternVerificationError,
    _find_pattern,
    _residual_prompt_values,
    _remaining_original_cases,
    _samples_with_numbered_context,
    run_residual_loop,
)
from agents.contracts import (
    DdlTransform,
    OpMapping,
    ResidualPatternResult,
    ScanResult,
    TableAccessPattern,
    WrapperKeyExclusionRule,
)
from graph.schema.graph_edges import ReadsWrites
from pipeline.steps.step5_linking.link_tables.link_residual_patterns_step import (
    link_residual_patterns_step,
)


def _case() -> dict:
    return {
        "case_id": "case-1",
        "file": "sample.c",
        "line": 1,
        "call_text": "SQL_CALL",
        "source_id": "code:sample.c:fn",
        "attempted_patterns": [],
    }


def _scan() -> ScanResult:
    return ScanResult(
        confidence=1.0,
        evidence="test",
        evidence_locations=["sample.c:1", "sample.c:2", "sample.c:3"],
        no_match_reason="residual test has no initial pattern",
    )


class ResidualResilienceTest(IsolatedAsyncioTestCase):
    async def test_step_quarantines_rejected_candidate_and_preserves_residual(self) -> None:
        cases = [_case()]
        config = {"configurable": {
            "codebase": SimpleNamespace(root=Path(".")),
            "cost_budget": CostBudget(limit_usd=1.0),
            "neo_client": None,
            "stream": None,
        }}
        with patch(
            "pipeline.steps.step5_linking.link_tables.link_residual_patterns_step.run_residual_loop",
            new=AsyncMock(side_effect=ResidualPatternVerificationError("dead rule")),
        ):
            result = await link_residual_patterns_step(
                {
                    "unresolved_cases": cases,
                    "scan_result": _scan().model_dump(),
                    "code_nodes": [],
                    "tables": [],
                },
                config,
            )

        self.assertEqual([], result["reads_writes_edges"])
        self.assertEqual(cases, result["unresolved_cases"])

    async def test_matched_skill_preserves_residual_without_runtime_override(self) -> None:
        cases = [
            _case(),
            {
                **_case(),
                "case_id": "case-2",
                "line": 2,
                "source_id": None,
                "attempted_patterns": [{
                    "pattern_id": "documented",
                    "groups": {"entity": "MISSING_TABLE"},
                    "ddl_lookup_result": "not_found",
                }],
            },
        ]
        scan = _scan().model_copy(update={
            "matched_skill": "documented-skill",
            "no_match_reason": None,
        })
        link = AsyncMock(side_effect=AssertionError("known SKILL must not be rescanned"))
        find = AsyncMock(side_effect=AssertionError("known SKILL must not call residual LLM"))

        with (
            patch("agents.residual.RegexTableLinker.link", new=link),
            patch("agents.residual._find_pattern", new=find),
        ):
            outcome = await run_residual_loop(
                unresolved_cases=cases,
                scan_result=scan,
                project_root=Path("."),
                code_nodes=[],
                tables=[],
                cost_budget=CostBudget(limit_usd=1.0),
            )

        link.assert_not_awaited()
        find.assert_not_awaited()
        self.assertEqual([], outcome.edges)
        self.assertEqual(cases, outcome.residual)
        self.assertIs(cases[0], outcome.residual[0])
        self.assertIs(cases[1], outcome.residual[1])
        self.assertEqual(0, outcome.rounds_used)
        self.assertEqual(0, outcome.patterns_found)

    async def test_pattern_proposal_is_one_yaml_rule_call(self) -> None:
        expected = ResidualPatternResult(
            table_access_patterns=[],
            ddl_transform_override=None,
            no_pattern_reason="no common syntax",
        )
        invoke = AsyncMock(return_value=expected)

        with (
            patch("agents.residual.run_yaml_rule", new=invoke),
            patch(
                "agents.residual._samples_with_numbered_context",
                return_value=[_case()],
            ),
        ):
            result = await _find_pattern(
                sample=[_case()],
                scan=_scan(),
                ddl_transform=DdlTransform(ddl_naming_rule="as_is"),
                project_root=Path("."),
                tables=[],
                cost_budget=CostBudget(limit_usd=1.0),
                round_no=1,
            )

        self.assertIs(expected, result)
        invoke.assert_awaited_once()
        kwargs = invoke.await_args.kwargs
        self.assertEqual("residual", kwargs["rule_name"])
        self.assertIs(ResidualPatternResult, kwargs["response_model"])

    def test_residual_result_requires_exactly_proposal_or_reason(self) -> None:
        from pydantic import ValidationError

        with self.assertRaises(ValidationError):
            ResidualPatternResult(
                table_access_patterns=[],
                ddl_transform_override=None,
                no_pattern_reason="",
            )
        with self.assertRaises(ValidationError):
            ResidualPatternResult(
                table_access_patterns=[],
                ddl_transform_override=DdlTransform(ddl_naming_rule="as_is"),
                no_pattern_reason="contradictory",
            )

        transform_only = ResidualPatternResult(
            table_access_patterns=[],
            ddl_transform_override=DdlTransform(ddl_naming_rule="camel_to_snake_upper"),
            no_pattern_reason=None,
        )
        self.assertEqual(
            "camel_to_snake_upper",
            transform_only.ddl_transform_override.ddl_naming_rule,
        )

    def test_prompt_deduplicates_attempt_regex_without_losing_case_evidence(self) -> None:
        pattern = TableAccessPattern(
            id="existing",
            call_regex=r"(?P<op_token>SELECT) (?P<entity>\w+)",
            op_mappings=[OpMapping(token="SELECT", op="SELECT")],
        )
        scan = _scan().model_copy(update={
            "matched_skill": "documented-skill",
            "no_match_reason": None,
            "table_access_patterns": [pattern],
            "table_access_exclusion_rules": [WrapperKeyExclusionRule(
                id="non_table_key", wrapper="db_exec", entity_suffix="_seq",
                separator="_", op_tokens=["r"], suffix_digits=3,
            )],
        })
        repeated = {
            "pattern_id": "existing",
            "regex": pattern.call_regex,
            "result": "matched",
            "groups": {"op_token": "SELECT", "entity": "ORDERS"},
            "ddl_lookup_result": "not_found",
        }
        sample = [{
            **_case(),
            "attempted_patterns": [repeated],
            "source_context_status": "ok",
            "source_coordinate_system": "absolute-file-line",
            "numbered_source_context": "1 | SQL_CALL",
        }]

        values = _residual_prompt_values(
            sample, scan, DdlTransform(ddl_naming_rule="as_is"), [], round_no=1,
        )

        self.assertEqual(
            pattern.call_regex,
            json.loads(values["confirmed_patterns"])[0]["call_regex"],
        )
        self.assertNotIn('"regex"', values["residual_cases"])
        self.assertIn('"entity": "ORDERS"', values["residual_cases"])
        self.assertIn("1 | SQL_CALL", values["residual_cases"])
        exclusions = json.loads(values["confirmed_exclusions"])
        self.assertEqual("non_table_key", exclusions[0]["id"])
        self.assertEqual("_seq", exclusions[0]["entity_suffix"])

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
                    scan_result=_scan(),
                    project_root=Path("."),
                    code_nodes=[],
                    tables=[],
                    cost_budget=CostBudget(limit_usd=1.0),
                )

    async def test_rejected_proposal_is_fatal_not_unresolved_success(self) -> None:
        baseline = SimpleNamespace(edges=[], unresolved=[])
        proposed = ResidualPatternResult(
            table_access_patterns=[{
                "id": "dead",
                "call_regex": r"(?P<op_token>SELECT) (?P<entity>ORDERS)",
                "op_mappings": [{"token": "SELECT", "op": "SELECT"}],
            }],
            ddl_transform_override=None,
            no_pattern_reason=None,
        )
        verdict = SimpleNamespace(ok=False, summary="dead rule")
        with (
            patch(
                "agents.residual.RegexTableLinker.link",
                new=AsyncMock(return_value=baseline),
            ),
            patch(
                "agents.residual._find_pattern",
                new=AsyncMock(return_value=proposed),
            ),
            patch(
                "agents.residual.verify_table_patterns",
                new=AsyncMock(return_value=verdict),
            ),
        ):
            with self.assertRaisesRegex(ResidualPatternVerificationError, "dead rule"):
                await run_residual_loop(
                    unresolved_cases=[_case()],
                    scan_result=_scan(),
                    project_root=Path("."),
                    code_nodes=[],
                    tables=[],
                    cost_budget=CostBudget(limit_usd=1.0),
                )

    async def test_transform_only_proposal_verifies_attempted_pattern_and_resolves(self) -> None:
        case = {
            **_case(),
            "attempted_patterns": [{"pattern_id": "existing"}],
        }
        pattern = TableAccessPattern(
            id="existing",
            call_regex=r"(?P<op_token>SELECT) (?P<entity>orders)",
            op_mappings=[OpMapping(token="SELECT", op="SELECT")],
        )
        scan = _scan().model_copy(update={"table_access_patterns": [pattern]})
        proposal = ResidualPatternResult(
            table_access_patterns=[],
            ddl_transform_override=DdlTransform(ddl_naming_rule="camel_to_snake_upper"),
            no_pattern_reason=None,
        )
        edge = ReadsWrites(
            source_id=case["source_id"],
            target_id="table:ORDERS",
            relation="READS",
            evidence_file=case["file"],
            evidence_line=case["line"],
        )
        baseline = SimpleNamespace(edges=[], resolved_sites=set())
        applied = SimpleNamespace(edges=[edge], resolved_sites=set())
        link = AsyncMock(side_effect=[baseline, applied])
        verify = AsyncMock(return_value=SimpleNamespace(ok=True, summary="verified"))

        with (
            patch("agents.residual.RegexTableLinker.link", new=link),
            patch("agents.residual._find_pattern", new=AsyncMock(return_value=proposal)),
            patch("agents.residual.verify_table_patterns", new=verify),
        ):
            outcome = await run_residual_loop(
                unresolved_cases=[case],
                scan_result=scan,
                project_root=Path("."),
                code_nodes=[],
                tables=[],
                cost_budget=CostBudget(limit_usd=1.0),
            )

        self.assertEqual([], outcome.residual)
        self.assertEqual([edge], outcome.edges)
        self.assertEqual(0, outcome.patterns_found)
        verify.assert_awaited_once()
        self.assertEqual([pattern], verify.await_args.kwargs["patterns"])
        self.assertEqual(
            "camel_to_snake_upper",
            verify.await_args.kwargs["ddl_transform"].ddl_naming_rule,
        )

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
