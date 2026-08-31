"""The frame wire owns prose only; parser/linker metadata remains immutable."""
from __future__ import annotations

import asyncio
import json
import unittest

from pydantic import ValidationError

from graph.schema.graph_nodes import CodeNode
from llm.runners.model_output_error import ModelOutputError
from pipeline.models.analysis_context import (
    AnalysisContext,
    ControlSubtreeNode,
    ExternalEvidenceContext,
    ReferencedSymbolContext,
    StatementFact,
)
from pipeline.models.data_object_usage import DataObjectUsageEvidence
from pipeline.models.semantic_frame import (
    EvidenceFact,
    SemanticFrame,
    SemanticSlot,
    evidence_id,
    mark_semantic_frame_insufficient,
    merge_semantic_frames,
    profile_prompt_json,
    target_digest,
)
from pipeline.semantic_tasks.summary.response_schema import (
    frame_summary_response_model,
    normalize_frame_summary_response,
)
from pipeline.models.rule_materialization import materialize_rule_items
from pipeline.quality_gates.branch_inventory import build_inventory
from pipeline.quality_gates.rule_prompt_evidence import rule_evidence
from pipeline.semantic_tasks.semantic_frame_planner import (
    build_code_semantic_plan,
    semantic_concern_slots,
)
from pipeline.semantic_tasks.three_split_llm_fanout import (
    _run_frame_summary,
)
from tests.pipeline.parser_call_fixtures import parser_call_occurrences


def _routine() -> CodeNode:
    return CodeNode(
        id="code:orders.save",
        signature="save(order_id, qty) -> int",
        file_path="src/orders.c",
        start_line=10,
        end_line=16,
        label="FUNCTION",
        name="save",
        owner_id="code:orders",
        params="order_id, qty",
        return_type="int",
    )


def _context() -> AnalysisContext:
    context = AnalysisContext(
        id="code:orders.save",
        signature="save(order_id, qty) -> int",
        file_path="src/orders.c",
        start_line=10,
        end_line=16,
        direct_reads=("db.orders",),
        reads=("db.orders",),
        callees=("code:stock.reserve",),
        data_object_usages=(
            DataObjectUsageEvidence(
                source_id="code:orders.save",
                raw_reference="orders",
                name="orders",
                access="READ",
                evidence_file="src/orders.c",
                evidence_line=11,
                table_id="db.orders",
                resolution="exact",
            ),
        ),
        referenced_symbols=(ReferencedSymbolContext(
            id="code:orders.ORDER_LIMIT",
            name="ORDER_LIMIT",
            kind="constant",
            data_type="int",
            declared_value="100",
            value_semantics="immutable_constant",
        ),),
    )
    context.extra["ast_statements"] = (
        StatementFact(
            node_id="code:orders.save:RETURN@16",
            label="RETURN",
            start=7,
            end=7,
            expression="rc",
        ),
    )
    context.extra["ast_call_occurrences"] = parser_call_occurrences(((4, "reserve"),))
    context.extra["table_op"] = {"db.orders": "SELECT"}
    return context


class StableIdentityTest(unittest.TestCase):
    def test_target_and_evidence_ids_do_not_depend_on_source_or_meaning(self):
        target = "code:orders.save"
        self.assertEqual(target_digest(target), target_digest(target))
        self.assertEqual(
            evidence_id(target, "CALL", {"line": 13, "name": "reserve"}),
            evidence_id(target, "CALL", {"name": "reserve", "line": 13}),
        )
        self.assertNotIn("reserve", evidence_id(target, "CALL", {"line": 13, "name": "reserve"}))

    def test_call_dependency_status_is_closed(self):
        with self.assertRaisesRegex(ValidationError, "dependency_status"):
            EvidenceFact(
                id=evidence_id("code:orders.save", "CALL", {"callee": "x"}),
                kind="CALL",
                owner_id="code:orders.save",
                attributes={"dependency_status": "quietly_skipped"},
            )

class SemanticFramePlannerTest(unittest.TestCase):
    def test_rule_fact_links_owned_effects_and_structural_relations(self):
        fn = CodeNode(
            id="code:f", signature="f(int rc)", file_path="src/f.c",
            start_line=100, end_line=107, label="FUNCTION", name="f",
            owner_id="code:file", params="int rc", return_type="int",
        )
        context = AnalysisContext(
            id=fn.id, signature=fn.signature, file_path=fn.file_path,
            start_line=fn.start_line, end_line=fn.end_line,
        )
        statements = (
            StatementFact("b4", "BREAK", 4, 4, target="break"),
            StatementFact("l5", "LABEL", 5, 5, target="PFM_CATCH"),
            StatementFact("r6", "RETURN", 6, 6, expression="RC_ERR"),
        )
        context.extra["ast_statements"] = statements
        context.extra["ast_call_occurrences"] = ()
        source = "\n".join((
            "switch (rc) {", "case RC_NFD:", "case RC_NRM:", "break;",
            "PFM_CATCH:", "return RC_ERR;", "}", "",
        ))
        tree = [ControlSubtreeNode(
            "switch@1", "SWITCH", 1, 7, expression="rc",
            children=[
                ControlSubtreeNode("case@2", "CASE", 2, 2, expression="RC_NFD"),
                ControlSubtreeNode("case@3", "CASE", 3, 4, expression="RC_NRM"),
            ],
        )]
        inventory = build_inventory(
            source, tree, "c", line_offset=99, statements=statements,
            handler_markers=("PFM_CATCH",),
        )

        prompt_evidence = rule_evidence(inventory, (100,))
        parent_row = prompt_evidence["rows"]["R100"]
        self.assertTrue(parent_row["nested_control"])
        self.assertEqual([
            {
                "node_id": "case@2",
                "type": "CASE",
                "start_line": 101,
                "end_line": 101,
                "condition": "RC_NFD",
                "condition_timing": "",
            },
            {
                "node_id": "case@3",
                "type": "CASE",
                "start_line": 102,
                "end_line": 103,
                "condition": "RC_NRM",
                "condition_timing": "",
            },
        ], parent_row["nested_children"])
        self.assertNotIn("direct", parent_row)

        profile, _specs = build_code_semantic_plan(fn, context, inventory=inventory)
        rules = {
            fact.attributes["anchor_line"]: fact
            for fact in profile.evidence.values() if fact.kind == "RULE"
        }
        self.assertEqual(102, rules[101].attributes["fallthrough_to"])
        self.assertTrue(rules[100].attributes["nested_control"])
        nested_refs = rules[100].attributes["source_refs"]
        self.assertTrue(nested_refs)
        self.assertTrue(all(
            profile.evidence[ref].kind == "CONTROL" for ref in nested_refs
        ))
        self.assertEqual(
            {"case@2", "case@3"},
            {profile.evidence[ref].attributes["node_id"] for ref in nested_refs},
        )
        handler_refs = rules[104].attributes["source_refs"]
        self.assertEqual(1, len(handler_refs))
        self.assertEqual("RETURN", profile.evidence[handler_refs[0]].attributes["type"])
        summary_rule_refs = [
            ref
            for spec in _specs
            if spec.family == "FUNCTION" and spec.role == "WHOLE_FUNCTION"
            for ref in spec.evidence_refs
            if profile.evidence[ref].kind == "RULE"
        ]
        expected_guard_refs = {
            ref for ref, fact in profile.evidence.items()
            if fact.kind == "RULE"
            and fact.attributes["role"] in {"decision", "complement", "handler"}
        }
        self.assertTrue(expected_guard_refs.issubset(set(summary_rule_refs)))
        self.assertEqual(len(summary_rule_refs), len(set(summary_rule_refs)))
        self.assertFalse(any(spec.family == "RULE" for spec in _specs))

    def test_planner_uses_actual_structure_and_does_not_invent_call_argument_slots(self):
        source = """int save(int order_id, int qty) {
if (qty > ORDER_LIMIT) {
  return -1;
}
reserve(order_id, qty);
return rc;
}"""
        inventory = build_inventory(
            source,
            [ControlSubtreeNode(
                node_id="code:orders.save:IF@11",
                ntype="IF",
                start=2,
                end=4,
                expression="qty > ORDER_LIMIT",
            )],
            "c",
            line_offset=9,
            statements=_context().extra["ast_statements"],
            call_occurrences=_context().extra["ast_call_occurrences"],
        )
        profile, specs = build_code_semantic_plan(
            _routine(), _context(), inventory=inventory,
        )

        roles = {(spec.family, spec.role) for spec in specs}
        self.assertIn(("FUNCTION", "WHOLE_FUNCTION"), roles)
        self.assertNotIn(("FUNCTION", "ORDERED_FLOW"), roles)
        self.assertFalse(any(family == "RULE" for family, _role in roles))
        self.assertFalse(any(
            family in {"DECISION", "LOOP", "EXCEPTION"}
            for family, _role in roles
        ))
        self.assertNotIn(("CALL", "PURPOSE"), roles)
        self.assertTrue(any(fact.kind == "CALL" for fact in profile.evidence.values()))
        self.assertIn(("SELECT", "PURPOSE"), roles)
        self.assertNotIn(("CALL", "ARGUMENT_ROLE"), roles)
        self.assertNotIn(("CALL", "RESULT_USE"), roles)

        self.assertEqual("structural-profile/v1", profile.schema_version)
        self.assertTrue(profile.ordered_flow)
        self.assertTrue(profile.callee_refs)
        self.assertTrue(profile.data_object_refs)
        self.assertTrue(profile.symbol_refs)
        self.assertFalse(any(
            "int save" in str(fact.model_dump()) for fact in profile.evidence.values()
        ))

    def test_dbms_call_target_owns_call_meaning_but_framework_occurrences_do_not(self):
        call = CodeNode(
            id="code:pkg.proc:CALL@22",
            signature="CALL notify_customer",
            file_path="pkg.sql",
            start_line=22,
            end_line=22,
            label="CALL",
            name="notify_customer",
            owner_id="code:pkg.proc",
        )
        context = AnalysisContext(
            id=call.id,
            signature=call.signature,
            file_path=call.file_path,
            start_line=call.start_line,
            end_line=call.end_line,
        )
        profile, specs = build_code_semantic_plan(call, context)

        self.assertEqual({("CALL", "PURPOSE")}, {
            (spec.family, spec.role) for spec in specs
        })
        self.assertFalse(profile.callee_refs)

    def test_cycle_callee_is_an_explicit_obligation_and_cannot_close_sufficient(self):
        context = _context()
        context.extra["unavailable_callee_dependencies"] = (
            "code:stock.reserve",
        )
        profile, specs = build_code_semantic_plan(_routine(), context)

        unavailable_refs = {
            fact.id
            for fact in profile.evidence.values()
            if fact.kind == "CALL"
            and fact.attributes.get("dependency_status") == "unavailable_cycle"
        }
        self.assertEqual(1, len(unavailable_refs))
        dependency_specs = tuple(
            spec for spec in specs
            if unavailable_refs.intersection(spec.evidence_refs)
        )
        self.assertEqual(1, len(dependency_specs))
        prompt_profile = json.loads(profile_prompt_json(profile, dependency_specs))
        self.assertEqual(
            "unavailable_cycle",
            next(
                fact["dependency_status"]
                for fact in prompt_profile["facts"]
                if fact["kind"] == "CALL"
                and fact.get("dependency_status")
            ),
        )

        model = frame_summary_response_model("framework", dependency_specs)
        output = model.model_validate({
            "summary": "예약 호출의 결과는 cycle 때문에 확인할 수 없다.",
            "stereotype": "ApplicationService",
        })
        summary = normalize_frame_summary_response(output, profile, dependency_specs)
        dependency_slot = summary.semantic_frame.slots[dependency_specs[0].slot_id]

        self.assertEqual("partial", dependency_slot.status)
        self.assertNotIn("code:stock.reserve", " ".join(dependency_slot.missing_context))
        self.assertIn("call cycle", " ".join(dependency_slot.missing_context))

    def test_projection_and_rejection_preserve_every_distinct_diagnostic(self):
        profile, specs = build_code_semantic_plan(_routine(), _context())
        summary_specs = semantic_concern_slots(specs)["summary"]
        model = frame_summary_response_model("framework", summary_specs)
        missing = [f"근거 의무 {index}" for index in range(24)]
        parsed = model.model_validate({
            "summary": "확정된 입력 범위의 처리를 수행한다.",
            "stereotype": "ApplicationService",
        })
        projected = normalize_frame_summary_response(parsed, profile, summary_specs)
        frame = projected.semantic_frame

        rejected = mark_semantic_frame_insufficient(frame, missing)
        self.assertTrue(all(
            slot.missing_context == tuple(missing)
            for slot in rejected.slots.values()
        ))

    def test_control_slots_use_absolute_statement_coordinates(self):
        source = """int save(int order_id, int qty) {
if (qty > ORDER_LIMIT) {
  return -1;
}
return 0;
}"""
        context = _context()
        context.extra["ast_statements"] = (
            StatementFact(
                node_id="code:orders.save:RETURN@11",
                label="RETURN", start=2, end=3, expression="-1",
            ),
        )
        inventory = build_inventory(
            source,
            [ControlSubtreeNode(
                node_id="code:orders.save:IF@11", ntype="IF",
                start=2, end=4, expression="qty > ORDER_LIMIT",
            )],
            "c", line_offset=9,
            statements=context.extra["ast_statements"],
            call_occurrences=(),
        )
        profile, specs = build_code_semantic_plan(_routine(), context, inventory=inventory)
        rule_fact = next(
            fact for fact in profile.evidence.values()
            if fact.kind == "RULE" and fact.attributes["anchor_line"] == 11
        )
        statement_facts = [
            profile.evidence[ref] for ref in rule_fact.attributes["source_refs"]
            if profile.evidence[ref].kind == "STATEMENT"
        ]
        self.assertEqual([11], [fact.start_line for fact in statement_facts])
        self.assertEqual([], semantic_concern_slots(specs).get("rules", []))

    def test_rule_rows_are_structural_facts_not_semantic_slots(self):
        source = """int save(int order_id, int qty) {
if (qty > ORDER_LIMIT) {
  return -1;
}
return 0;
}"""
        context = _context()
        context.extra["ast_statements"] = (
            StatementFact(
                node_id="code:orders.save:RETURN@11",
                label="RETURN", start=2, end=3, expression="-1",
            ),
            StatementFact(
                node_id="code:orders.save:RETURN@14",
                label="RETURN", start=5, end=5, expression="0",
            ),
        )
        inventory = build_inventory(
            source,
            [ControlSubtreeNode(
                node_id="code:orders.save:IF@11", ntype="IF",
                start=2, end=4, expression="qty > ORDER_LIMIT",
            )],
            "c", line_offset=9,
            statements=context.extra["ast_statements"],
            call_occurrences=(),
        )

        all_rows = [int(row["line"]) for row in inventory.table_rows()]
        self.assertGreaterEqual(len(all_rows), 2)
        allowed_rows = tuple(all_rows)
        profile, specs = build_code_semantic_plan(
            _routine(), context, inventory=inventory, rule_rows=allowed_rows,
        )
        self.assertEqual([], semantic_concern_slots(specs).get("rules", []))
        anchors = {
            int(fact.attributes["anchor_line"])
            for fact in profile.evidence.values()
            if fact.kind == "RULE"
        }
        self.assertEqual(set(allowed_rows), anchors)

        rules = materialize_rule_items(
            rule_evidence(inventory, allowed_rows), allowed_rows,
        )
        self.assertEqual(list(allowed_rows), [rule.anchor_line for rule in rules])
        self.assertTrue(any(rule.structural_condition for rule in rules))

    def test_exception_propagation_uses_the_owned_branch_termination_fact(self):
        source = """BEGIN
  SELECT status_cd INTO v_status_cd FROM order_status;
EXCEPTION
  WHEN NO_DATA_FOUND THEN
    RETURN 'UNKNOWN';
END;"""
        context = _context()
        context.extra["ast_statements"] = (
            StatementFact(
                node_id="code:orders.save:RETURN@14",
                label="RETURN", start=5, end=5, expression="'UNKNOWN'",
            ),
        )
        inventory = build_inventory(
            source,
            [ControlSubtreeNode(
                node_id="code:orders.save:EXCEPTION@12", ntype="EXCEPTION",
                start=3, end=6, expression="NO_DATA_FOUND",
            )],
            "sql", line_offset=9,
            statements=context.extra["ast_statements"],
            call_occurrences=(),
        )
        handler_row = next(
            int(row["line"]) for row in inventory.table_rows()
            if row["role"] == "handler"
        )

        profile, specs = build_code_semantic_plan(
            _routine(), context, inventory=inventory, rule_rows=(handler_row,),
        )
        self.assertEqual([], semantic_concern_slots(specs).get("rules", []))
        rule_fact = next(
            fact for fact in profile.evidence.values()
            if fact.kind == "RULE"
            and int(fact.attributes["anchor_line"]) == handler_row
        )
        self.assertTrue(any(
            profile.evidence[ref].kind == "STATEMENT"
            and profile.evidence[ref].attributes["type"] == "RETURN"
            for ref in rule_fact.attributes["source_refs"]
        ))
        rule = materialize_rule_items(
            rule_evidence(inventory, (handler_row,)), (handler_row,),
        )[0]
        self.assertIn("ret", " ".join(rule.structural_effects))

    def test_direct_child_structure_participates_in_ordered_flow(self):
        child = CodeNode(
            id="code:orders.save:IF@12", signature="if qty > 0",
            file_path="src/orders.c", start_line=12, end_line=14,
            label="IF", name="", owner_id="code:orders.save",
            expression="qty > 0",
        )
        profile, _specs = build_code_semantic_plan(
            _routine(), _context(), child_nodes=(child,),
        )
        self.assertEqual(1, len(profile.child_refs))
        self.assertIn(profile.child_refs[0], profile.ordered_flow)
        self.assertEqual("CONTROL", profile.evidence[profile.child_refs[0]].kind)

    def test_every_processing_statement_is_assigned_to_exactly_one_summary_obligation(self):
        context = _context()
        context.extra["ast_statements"] = tuple(
            StatementFact(
                node_id=f"code:orders.save:ASSIGNMENT@{line}",
                label="ASSIGNMENT", start=line, end=line,
                expression=str(line), target=f"v{line}", operator="=",
            )
            for line in range(1, 501)
        )
        context.extra["ast_call_occurrences"] = parser_call_occurrences(
            (line, f"call_{line}") for line in range(1, 11)
        )
        profile, specs = build_code_semantic_plan(_routine(), context)
        summary_specs = semantic_concern_slots(specs)["summary"]

        full = json.loads(profile_prompt_json(profile))
        projected = json.loads(profile_prompt_json(profile, summary_specs))
        assigned_refs = {
            ref
            for spec in summary_specs
            for ref in (spec.target_ref, *spec.evidence_refs)
        }
        statement_refs = {
            ref for ref, fact in profile.evidence.items()
            if fact.kind == "STATEMENT" and fact.attributes["type"] == "ASSIGNMENT"
        }
        processing_refs = [
            ref
            for spec in summary_specs
            if spec.family == "FUNCTION" and spec.role == "WHOLE_FUNCTION"
            for ref in spec.evidence_refs
            if ref in statement_refs
        ]

        self.assertIsInstance(projected["facts"], list)
        self.assertGreater(len(full["evidence"]), 500)
        self.assertEqual(statement_refs, set(processing_refs))
        self.assertEqual(len(statement_refs), len(processing_refs))

    def test_routine_responsibility_uses_target_range_without_duplicate_lineage(self):
        profile, specs = build_code_semantic_plan(_routine(), _context())
        target_ref = next(
            ref for ref, fact in profile.evidence.items() if fact.kind == "TARGET"
        )
        for spec in specs:
            if spec.family == "FUNCTION" and spec.role == "RESPONSIBILITY":
                self.assertEqual((target_ref,), spec.evidence_refs)

    def test_ordered_flow_resolves_exact_profile_facts_without_aggregate_prose(self):
        profile, specs = build_code_semantic_plan(_routine(), _context())

        self.assertTrue(profile.ordered_flow)
        self.assertTrue(all(ref in profile.evidence for ref in profile.ordered_flow))
        self.assertFalse(any(
            spec.family == "FUNCTION" and spec.role == "ORDERED_FLOW"
            for spec in specs
        ))

    def test_framework_call_occurrences_each_own_one_processing_obligation(self):
        context = _context()
        context.extra["ast_call_occurrences"] = parser_call_occurrences(
            (line, f"call_{line}") for line in range(1, 71)
        )
        profile, specs = build_code_semantic_plan(_routine(), context)
        summary_specs = semantic_concern_slots(specs)["summary"]

        assigned = [spec.slot_id for spec in summary_specs]

        call_refs = {
            ref for ref, fact in profile.evidence.items()
            if fact.kind == "CALL" and fact.start_line > 0
        }
        processing_refs = [
            ref
            for spec in summary_specs
            if spec.family == "FUNCTION" and spec.role == "WHOLE_FUNCTION"
            for ref in spec.evidence_refs
            if ref in call_refs
        ]

        self.assertEqual(1, len(summary_specs))
        self.assertEqual({spec.slot_id for spec in summary_specs}, set(assigned))
        self.assertEqual(len(assigned), len(set(assigned)))
        self.assertEqual(call_refs, set(processing_refs))
        self.assertEqual(len(call_refs), len(processing_refs))
        self.assertEqual(70, len(call_refs))
        self.assertFalse(any(spec.family == "CALL" for spec in specs))

    def test_each_external_chunk_owns_one_exact_processing_obligation(self):
        context = _context()
        external = tuple(
            ExternalEvidenceContext(
                id=f"external:{index}",
                file_path=f"contracts/{index}.txt",
                matched_key=f"KEY.{index}",
                semantic_kind="contract",
                provenance="source_span",
                text=(f"exact external payload {index} " + "X" * 900),
                start_line=10 * index + 1,
                end_line=10 * index + 1,
                content_hash=f"hash-{index}",
            )
            for index in range(10)
        )
        context.external_evidence = external
        profile, specs = build_code_semantic_plan(_routine(), context)
        summary_specs = semantic_concern_slots(specs)["summary"]
        external_refs = {
            ref for ref, fact in profile.evidence.items() if fact.kind == "EXTERNAL"
        }
        assigned_refs = [
            ref
            for spec in summary_specs
            if spec.role == "WHOLE_FUNCTION"
            for ref in spec.evidence_refs
            if ref in external_refs
        ]

        self.assertEqual(10, len(external_refs))
        self.assertEqual(external_refs, set(assigned_refs))
        self.assertEqual(10, len(assigned_refs))

        observed_payloads: list[str] = []
        observed_external_refs: list[str] = []

        async def invoke(values, response_model):
            payload = json.loads(values["linked_external_sources"])
            self.assertTrue(all(set(item) == {"kind", "matched_key", "text"} for item in payload))
            observed_payloads.extend(item["text"] for item in payload)
            observed_external_refs.extend(item["matched_key"] for item in payload)
            response = {
                "summary": "외부 문맥을 사용해 대상을 처리한다.",
                "stereotype": "Validator",
            }
            return response_model.model_validate(response)

        asyncio.run(_run_frame_summary(
            profile=profile,
            slot_specs=summary_specs,
            strategy_name="framework",
            analysis_inputs={
                "raw_source_code": "\n".join(f"line {line}" for line in range(10, 17)),
                "external_source_text_by_id": {
                    item.id: item.text
                    for item in external
                },
            },
            invoke=invoke,
        ))

        self.assertCountEqual(
            [item.text for item in external],
            observed_payloads,
        )
        self.assertEqual(10, len(set(observed_external_refs)))

    def test_each_model_call_receives_exact_assigned_evidence_and_source_closure(self):
        profile, specs = build_code_semantic_plan(_routine(), _context())
        summary_specs = semantic_concern_slots(specs)["summary"]
        source_lines = [f"source line {line}" for line in range(10, 17)]
        source_lines[3] = r'''call("\\t", 'quoted');'''
        source = "\n".join(source_lines)
        observed_profiles: list[dict] = []

        async def invoke(values, response_model):
            prompt_profile = json.loads(values["structural_profile"])
            self.assertIsInstance(prompt_profile["facts"], list)

            expected_lines = list(range(10, 17))
            expected_source = "\n".join(
                f"{line} | {source_lines[line - 10]}" for line in expected_lines
            )
            self.assertEqual(expected_source, values["code_body"])
            self.assertEqual(
                {
                    "target_id",
                    "code_body", "structural_profile",
                    "linked_callee_outcomes", "linked_external_sources",
                },
                set(values),
            )
            observed_profiles.append(prompt_profile)
            payload = {
                "summary": "입력을 검사하고 처리 결과를 반환한다.",
                "stereotype": "Validator",
            }
            return response_model.model_validate(payload)

        result = asyncio.run(_run_frame_summary(
            profile=profile,
            slot_specs=summary_specs,
            strategy_name="framework",
            analysis_inputs={"raw_source_code": source},
            invoke=invoke,
        ))

        self.assertTrue(result.summary)
        self.assertEqual(1, len(observed_profiles))

    def test_routine_summary_roles_follow_actual_structure_and_stay_bounded(self):
        source = "int check(void *ctx) {\nreturn RC_NRM;\n}"
        fn = CodeNode(
            id="code:noop.check", signature="int check(void *ctx)",
            file_path="src/noop.c", start_line=1, end_line=3,
            label="FUNCTION", name="check", owner_id="code:noop",
            params="void *ctx", return_type="int",
        )
        context = AnalysisContext(
            id=fn.id, signature=fn.signature, file_path=fn.file_path,
            start_line=fn.start_line, end_line=fn.end_line,
        )
        context.extra["ast_statements"] = (
            StatementFact(
                node_id="code:noop.check:RETURN@2", label="RETURN",
                start=2, end=2, expression="RC_NRM",
            ),
        )
        context.extra["ast_call_occurrences"] = ()
        inventory = build_inventory(
            source, [], "c", statements=context.extra["ast_statements"],
        )

        _profile, specs = build_code_semantic_plan(
            fn, context, inventory=inventory,
        )
        roles = [
            spec.role for spec in semantic_concern_slots(specs)["summary"]
            if spec.family == "FUNCTION"
        ]

        self.assertEqual(["WHOLE_FUNCTION"], roles)

    def test_guard_and_processing_roles_are_independently_applicable(self):
        fn = CodeNode(
            id="code:roles.f", signature="int f(int value)",
            file_path="src/roles.c", start_line=1, end_line=4,
            label="FUNCTION", name="f", owner_id="code:roles",
            params="int value", return_type="int",
        )

        guard_context = AnalysisContext(
            id=fn.id, signature=fn.signature, file_path=fn.file_path,
            start_line=fn.start_line, end_line=fn.end_line,
        )
        guard_context.extra["ast_statements"] = (
            StatementFact("return@2", "RETURN", 2, 2, expression="RC_ERR"),
        )
        guard_context.extra["ast_call_occurrences"] = ()
        guard_inventory = build_inventory(
            "int f(int value) {\nif (value < 0) return RC_ERR;\nreturn RC_NRM;\n}",
            [ControlSubtreeNode(
                "if@2", "IF", 2, 2, expression="value < 0",
            )],
            "c", statements=guard_context.extra["ast_statements"],
        )

        _profile, guard_specs = build_code_semantic_plan(
            fn, guard_context, inventory=guard_inventory,
        )
        guard_roles = {spec.role for spec in guard_specs if spec.family == "FUNCTION"}
        self.assertEqual({"WHOLE_FUNCTION"}, guard_roles)

        effect_context = AnalysisContext(
            id=fn.id, signature=fn.signature, file_path=fn.file_path,
            start_line=fn.start_line, end_line=fn.end_line,
        )
        effect_context.extra["ast_statements"] = (
            StatementFact(
                "set@2", "ASSIGNMENT", 2, 2,
                expression="value + 1", target="result", operator="=",
            ),
            StatementFact("return@3", "RETURN", 3, 3, expression="result"),
        )
        effect_context.extra["ast_call_occurrences"] = ()

        _profile, effect_specs = build_code_semantic_plan(fn, effect_context)
        effect_roles = {spec.role for spec in effect_specs if spec.family == "FUNCTION"}
        self.assertEqual({"WHOLE_FUNCTION"}, effect_roles)

    def test_summary_projection_uses_complete_flow_role_order(self):
        source = """int save(int qty) {
if (qty <= 0) return RC_ERR;
reserve(qty);
return RC_NRM;
}"""
        context = _context()
        inventory = build_inventory(
            source,
            [ControlSubtreeNode(
                node_id="code:orders.save:IF@11", ntype="IF",
                start=2, end=2, expression="qty <= 0",
            )],
            "c", line_offset=9,
            statements=context.extra["ast_statements"],
            call_occurrences=context.extra["ast_call_occurrences"],
        )
        profile, specs = build_code_semantic_plan(
            _routine(), context, inventory=inventory,
        )
        summary_specs = semantic_concern_slots(specs)["summary"]
        model = frame_summary_response_model("framework", summary_specs)
        payload = {
            "summary": (
                "수량이 유효하지 않으면 오류로 종료하고, 유효하면 재고를 예약한 뒤 "
                "정상 결과를 반환한다."
            ),
            "stereotype": "ApplicationService",
        }

        summary = normalize_frame_summary_response(
            model.model_validate(payload), profile, summary_specs,
        )

        self.assertEqual(payload["summary"], summary.summary)
        self.assertNotIn("1 책임\n2 입력", summary.summary)

    def test_repeated_role_fragments_project_in_exact_source_order(self):
        context = _context()
        context.extra["ast_statements"] = (
            StatementFact(
                "set@2", "ASSIGNMENT", 2, 2,
                expression="first", target="a", operator="=",
            ),
            StatementFact(
                "set@3", "ASSIGNMENT", 3, 3,
                expression="second", target="b", operator="=",
            ),
            StatementFact("return@7", "RETURN", 7, 7, expression="b"),
        )
        context.extra["ast_call_occurrences"] = ()
        context.data_object_usages = ()
        context.callees = ()
        profile, specs = build_code_semantic_plan(_routine(), context)
        summary_specs = semantic_concern_slots(specs)["summary"]
        model = frame_summary_response_model("framework", summary_specs)

        parsed = model.model_validate({
            "summary": "첫 번째 값을 정한 뒤 두 번째 값을 계산해 반환한다.",
            "stereotype": "Validator",
        })
        projected = normalize_frame_summary_response(parsed, profile, summary_specs)

        self.assertEqual(
            "첫 번째 값을 정한 뒤 두 번째 값을 계산해 반환한다.",
            projected.summary,
        )

    def test_summary_schema_rejects_partitioned_detail_obligations(self):
        profile, specs = build_code_semantic_plan(_routine(), _context())
        primary = semantic_concern_slots(specs)["summary"]
        model = frame_summary_response_model("framework", primary)
        self.assertEqual({"summary", "stereotype"}, set(model.model_fields))
        with self.assertRaises(ValidationError):
            model.model_validate({
                "summary": "대상을 처리한다.",
                "stereotype": "Helper",
                "slots": {"W0": "불필요한 세부"},
            })

    def test_summary_deadline_is_terminal_without_second_meaning_request(self):
        profile, specs = build_code_semantic_plan(_routine(), _context())
        summary_specs = semantic_concern_slots(specs)["summary"]
        source = "\n".join(f"source line {line}" for line in range(10, 17))
        calls = 0

        async def invoke(values, response_model):
            nonlocal calls
            calls += 1
            raise ModelOutputError(
                rule_name="code_summary",
                kind="deadline",
                detail="test deadline",
                original_type="SyntheticProviderDeadline",
            )

        with self.assertRaises(ModelOutputError):
            asyncio.run(_run_frame_summary(
                profile=profile,
                slot_specs=summary_specs,
                strategy_name="framework",
                analysis_inputs={"raw_source_code": source},
                invoke=invoke,
            ))
        self.assertEqual(1, calls)

    def test_summary_repetition_is_terminal_without_second_meaning_request(self):
        profile, specs = build_code_semantic_plan(_routine(), _context())
        summary_specs = semantic_concern_slots(specs)["summary"]
        source = "\n".join(f"source line {line}" for line in range(10, 17))
        calls = 0

        async def invoke(values, response_model):
            nonlocal calls
            calls += 1
            raise ModelOutputError(
                rule_name="code_summary",
                kind="repetition",
                detail="test repetition",
                original_type="RepetitionLoopAborted",
            )

        with self.assertRaises(ModelOutputError):
            asyncio.run(_run_frame_summary(
                profile=profile,
                slot_specs=summary_specs,
                strategy_name="framework",
                analysis_inputs={"raw_source_code": source},
                invoke=invoke,
            ))
        self.assertEqual(1, calls)

    def test_concern_partition_is_total_disjoint_and_merge_requires_exact_population(self):
        profile, specs = build_code_semantic_plan(_routine(), _context())
        concerns = semantic_concern_slots(specs)
        assigned = [spec.slot_id for group in concerns.values() for spec in group]
        self.assertEqual({spec.slot_id for spec in specs}, set(assigned))
        self.assertEqual(len(assigned), len(set(assigned)))

        fragments = []
        for group in concerns.values():
            if not group:
                continue
            fragments.append(SemanticFrame(
                target=profile.target,
                profile=profile,
                slots={
                    spec.slot_id: SemanticSlot(
                        slot_id=spec.slot_id,
                        family=spec.family,
                        role=spec.role,
                        target_ref=spec.target_ref,
                        evidence_refs=spec.evidence_refs,
                        meaning=f"{spec.family}.{spec.role}",
                        status="sufficient",
                    )
                    for spec in group
                },
            ))
        merged = merge_semantic_frames(
            *fragments,
            required_slot_ids=(spec.slot_id for spec in specs),
        )
        self.assertEqual({spec.slot_id for spec in specs}, set(merged.slots))

    def test_whole_summary_is_model_owned_while_gap_metadata_comes_from_frame(self):
        profile, specs = build_code_semantic_plan(_routine(), _context())
        summary_specs = semantic_concern_slots(specs)["summary"]
        model = frame_summary_response_model("framework", summary_specs)
        payload = {
            "summary": "주문을 검증한 뒤 저장 처리를 수행한다.",
            "stereotype": "ApplicationService",
        }
        parsed = model.model_validate(payload)
        summary = normalize_frame_summary_response(parsed, profile, summary_specs)
        self.assertIsNotNone(summary.semantic_frame)
        self.assertEqual("sufficient", summary.evidence_status)
        self.assertEqual(payload["summary"], summary.summary)
        self.assertNotIn("FUNCTION.RESPONSIBILITY", summary.summary)


if __name__ == "__main__":
    unittest.main()
