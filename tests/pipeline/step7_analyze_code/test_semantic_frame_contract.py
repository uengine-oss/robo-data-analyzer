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
    ReferencedSymbolContext,
    StatementFact,
)
from pipeline.models.data_object_usage import DataObjectUsageEvidence
from pipeline.models.semantic_frame import (
    SlotSpec,
    evidence_id,
    normalize_semantic_frame_response,
    semantic_frame_response_model,
    semantic_slot_wire_ids,
    target_digest,
    merge_semantic_frames,
    profile_prompt_json,
    slot_specs_json,
)
from pipeline.models.summary_response_schema import (
    frame_summary_response_model,
    normalize_frame_summary_response,
)
from pipeline.models.rule_materialization import materialize_rule_items
from pipeline.quality_gates.branch_inventory import build_inventory
from pipeline.quality_gates.rule_prompt_evidence import rule_evidence
from pipeline.steps.step7_analyze_code.semantic_frame_planner import (
    build_code_semantic_plan,
    semantic_concern_slots,
)
from pipeline.steps.step7_analyze_code.three_split_llm_fanout import (
    _run_bounded_frame_summary,
    _summary_slot_batches,
)


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
    context.extra["ast_call_lines"] = ((4, "reserve"),)
    context.extra["data_object_usages"] = (
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
    )
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
        context.extra["ast_call_lines"] = ()
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
        self.assertEqual(
            (),
            tuple(
                spec for spec in _specs
                if any(
                    profile.evidence[ref].kind == "RULE"
                    for ref in spec.evidence_refs
                )
            ),
        )

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
            call_lines=_context().extra["ast_call_lines"],
        )
        profile, specs = build_code_semantic_plan(
            _routine(), _context(), inventory=inventory,
        )

        roles = {(spec.family, spec.role) for spec in specs}
        self.assertIn(("FUNCTION", "RESPONSIBILITY"), roles)
        self.assertIn(("FUNCTION", "INPUT_ROLE"), roles)
        self.assertIn(("FUNCTION", "RESULT"), roles)
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

    def test_wire_requires_every_preassigned_slot_and_binds_metadata_outside_model(self):
        profile, specs = build_code_semantic_plan(_routine(), _context())
        model = semantic_frame_response_model(specs)
        wire_ids = semantic_slot_wire_ids(specs)
        payload = {
            "slots": {
                wire_id: {
                    "meaning": f"{spec.family}.{spec.role} 의미",
                    "status": "sufficient",
                    "missing_context": [],
                }
                for spec, wire_id in zip(specs, wire_ids, strict=True)
            },
        }
        parsed = model.model_validate(payload)
        frame = normalize_semantic_frame_response(parsed, profile, specs)

        self.assertEqual("semantic-frame/v1", frame.schema_version)
        self.assertEqual(set(wire_ids), set(payload["slots"]))
        self.assertEqual({spec.slot_id for spec in specs}, set(frame.slots))
        for spec in specs:
            self.assertEqual(spec.evidence_refs, frame.slots[spec.slot_id].evidence_refs)

        missing = {"slots": dict(payload["slots"])}
        missing["slots"].pop(wire_ids[0])
        with self.assertRaises(ValidationError):
            model.model_validate(missing)

        extra = {"slots": dict(payload["slots"])}
        extra["slots"]["S999"] = {
            "meaning": "창작 slot",
            "status": "sufficient",
            "missing_context": [],
        }
        with self.assertRaises(ValidationError):
            model.model_validate(extra)

    def test_provider_wire_uses_only_compact_local_keys_and_prompt_closes_mapping(self):
        _profile, specs = build_code_semantic_plan(_routine(), _context())
        model = semantic_frame_response_model(specs)
        wire_ids = semantic_slot_wire_ids(specs)
        schema_text = json.dumps(
            model.model_json_schema(), ensure_ascii=False, sort_keys=True,
        )
        assignment = json.loads(slot_specs_json(specs))

        self.assertEqual(list(wire_ids), [item["wire_id"] for item in assignment])
        self.assertEqual(list(wire_ids), list(
            model.model_fields["slots"].annotation.model_fields
        ))
        self.assertTrue(all(spec.slot_id not in schema_text for spec in specs))
        self.assertTrue(all("slot_id" not in item for item in assignment))

    def test_private_wire_leak_is_quarantined_without_rewriting_business_s0(self):
        profile, specs = build_code_semantic_plan(_routine(), _context())
        model = semantic_frame_response_model(specs)
        wire_ids = semantic_slot_wire_ids(specs)

        safe_payload = {"slots": {
            wire_id: {
                "meaning": "업무 상태 S0을 유지한다",
                "status": "sufficient",
                "missing_context": [],
            }
            for wire_id in wire_ids
        }}
        safe_frame = normalize_semantic_frame_response(
            model.model_validate(safe_payload), profile, specs,
        )
        self.assertTrue(all(
            slot.meaning == "업무 상태 S0을 유지한다"
            and slot.status == "sufficient"
            for slot in safe_frame.slots.values()
        ))

        leaked_payload = {"slots": dict(safe_payload["slots"])}
        leaked_payload["slots"][wire_ids[0]] = {
            "meaning": f"조건 {wire_ids[-1]}이면 처리한다",
            "status": "sufficient",
            "missing_context": [],
        }
        leaked_frame = normalize_semantic_frame_response(
            model.model_validate(leaked_payload), profile, specs,
        )
        leaked_slot = leaked_frame.slots[specs[0].slot_id]
        self.assertEqual("insufficient", leaked_slot.status)
        self.assertEqual("", leaked_slot.meaning)
        self.assertEqual(
            ("model output referenced a request-local private response key",),
            leaked_slot.missing_context,
        )
        self.assertTrue(all(
            slot.status == "sufficient"
            for slot_id, slot in leaked_frame.slots.items()
            if slot_id != specs[0].slot_id
        ))

        missing_leak_payload = {"slots": dict(safe_payload["slots"])}
        missing_leak_payload["slots"][wire_ids[0]] = {
            "meaning": "확인된 일부 의미",
            "status": "partial",
            "missing_context": [f"{wire_ids[0]}의 계약"],
        }
        missing_leak_frame = normalize_semantic_frame_response(
            model.model_validate(missing_leak_payload), profile, specs,
        )
        self.assertEqual(
            "insufficient",
            missing_leak_frame.slots[specs[0].slot_id].status,
        )

    def test_compact_wire_is_total_and_unique_across_boundary_sizes(self):
        for size in (1, 7, 8, 9, 24, 65):
            with self.subTest(size=size):
                specs = tuple(
                    SlotSpec(
                        slot_id=f"SL:0123456789ab:STATEMENT:DIRECT_ACTION:s{index}",
                        family="STATEMENT",
                        role="DIRECT_ACTION",
                        target_ref="EV:target",
                        evidence_refs=("EV:target",),
                    )
                    for index in range(size)
                )
                wire_ids = semantic_slot_wire_ids(specs)
                model = semantic_frame_response_model(specs)
                payload = {"slots": {
                    wire_id: {
                        "meaning": f"meaning {index}",
                        "status": "sufficient",
                        "missing_context": [],
                    }
                    for index, wire_id in enumerate(wire_ids)
                }}
                model.model_validate(payload)
                self.assertEqual(size, len(set(wire_ids)))
                namespace = wire_ids[0].rsplit("_", 1)[0]
                self.assertRegex(namespace, r"^W[0-9a-f]{10}$")
                self.assertEqual(
                    tuple(f"{namespace}_{index}" for index in range(size)),
                    wire_ids,
                )
                self.assertEqual(wire_ids, semantic_slot_wire_ids(specs))

                missing = {"slots": dict(payload["slots"])}
                missing["slots"].pop(wire_ids[size // 2])
                with self.assertRaises(ValidationError):
                    model.model_validate(missing)

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
            call_lines=(),
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
            call_lines=(),
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
        self.assertTrue(all(rule.nl == "" for rule in rules))
        self.assertTrue(any(rule.cond for rule in rules))

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
            call_lines=(),
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
        self.assertEqual("", rule.nl)
        self.assertIn("ret", " ".join(rule.then))

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

    def test_prompt_profile_is_bounded_to_assigned_slot_evidence(self):
        context = _context()
        context.extra["ast_statements"] = tuple(
            StatementFact(
                node_id=f"code:orders.save:ASSIGNMENT@{line}",
                label="ASSIGNMENT", start=line, end=line,
                expression=str(line), target=f"v{line}", operator="=",
            )
            for line in range(1, 501)
        )
        context.extra["ast_call_lines"] = tuple(
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

        self.assertEqual(assigned_refs, set(projected["evidence"]))
        self.assertGreater(len(full["evidence"]), 500)
        self.assertLess(len(projected["evidence"]), 20)
        self.assertLess(
            len(profile_prompt_json(profile, summary_specs)),
            len(profile_prompt_json(profile)) // 4,
        )

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

    def test_framework_call_occurrences_remain_evidence_without_expanding_summary(self):
        context = _context()
        context.extra["ast_call_lines"] = tuple(
            (line, f"call_{line}") for line in range(1, 71)
        )
        profile, specs = build_code_semantic_plan(_routine(), context)
        summary_specs = semantic_concern_slots(specs)["summary"]

        batches = _summary_slot_batches(summary_specs)
        assigned = [spec.slot_id for batch in batches for spec in batch]

        self.assertEqual(1, len(batches))
        self.assertLessEqual(len(summary_specs), 3)
        self.assertEqual({spec.slot_id for spec in summary_specs}, set(assigned))
        self.assertEqual(len(assigned), len(set(assigned)))
        self.assertTrue(any(spec.family == "FUNCTION" for spec in batches[0]))
        self.assertEqual(70, sum(
            fact.kind == "CALL" and fact.start_line > 0
            for fact in profile.evidence.values()
        ))
        self.assertFalse(any(spec.family == "CALL" for spec in specs))

    def test_summary_rejects_internal_meaning_ownership_expansion(self):
        primary = tuple(
            SlotSpec(
                slot_id=f"SL:0123456789ab:STATEMENT:DIRECT_ACTION:s{i}",
                family="STATEMENT",
                role="DIRECT_ACTION",
                target_ref="EV:target",
                evidence_refs=("EV:target",),
            )
            for i in range(30)
        )

        with self.assertRaisesRegex(
            ValueError, "summary concern exceeded target-owned semantic boundary",
        ):
            _summary_slot_batches(primary)

    def test_summary_deadline_splits_schema_without_repeating_or_losing_slots(self):
        profile, specs = build_code_semantic_plan(_routine(), _context())
        summary_specs = semantic_concern_slots(specs)["summary"]
        successful_ids: list[str] = []
        observed_common: list[tuple[str, str]] = []
        stereotype_batches: list[list[str]] = []

        async def invoke(values, response_model, _batch_label):
            observed_common.append((values["raw_code_body"], values["module_intent"]))
            assigned = json.loads(values["applicable_slots"])
            if len(assigned) > 2:
                raise ModelOutputError(
                    rule_name="code_summary",
                    kind="deadline",
                    detail="test deadline",
                    original_type="ModelGenerationDeadlineExceeded",
                )
            bindings = dict(response_model.__semantic_slot_bindings__)
            self.assertEqual(set(bindings), {item["wire_id"] for item in assigned})
            successful_ids.extend(spec.slot_id for spec in bindings.values())
            payload = {
                "slots": {
                    wire_id: {
                        "meaning": f"meaning for {spec.slot_id}",
                        "status": "sufficient",
                        "missing_context": [],
                    }
                    for wire_id, spec in bindings.items()
                },
            }
            if "stereotype" in response_model.model_fields:
                stereotype_batches.append([
                    spec.slot_id for spec in bindings.values()
                ])
                payload["stereotype"] = "Validator"
            return response_model.model_validate(payload)

        result = asyncio.run(_run_bounded_frame_summary(
            profile=profile,
            slot_specs=summary_specs,
            strategy_name="framework",
            common={"raw_code_body": "same complete source", "module_intent": "same intent"},
            invoke=invoke,
        ))

        self.assertTrue(result.summary)
        self.assertEqual(
            {spec.slot_id for spec in summary_specs},
            set(successful_ids),
        )
        self.assertEqual(len(successful_ids), len(set(successful_ids)))
        self.assertTrue(observed_common)
        self.assertEqual(
            {("same complete source", "same intent")},
            set(observed_common),
        )
        self.assertEqual(1, len(stereotype_batches))
        self.assertTrue(any(
            spec.family == "FUNCTION" and spec.role == "RESPONSIBILITY"
            for spec in summary_specs
            if spec.slot_id in stereotype_batches[0]
        ))

    def test_summary_repetition_splits_without_losing_shared_context_or_slots(self):
        profile, specs = build_code_semantic_plan(_routine(), _context())
        summary_specs = semantic_concern_slots(specs)["summary"]
        successful_ids: list[str] = []
        observed_common: list[tuple[str, str]] = []
        stereotype_batches: list[list[str]] = []

        async def invoke(values, response_model, _batch_label):
            observed_common.append((values["raw_code_body"], values["module_intent"]))
            assigned = json.loads(values["applicable_slots"])
            if len(assigned) > 2:
                raise ModelOutputError(
                    rule_name="code_summary",
                    kind="repetition",
                    detail="test repetition",
                    original_type="RepetitionLoopAborted",
                )
            bindings = dict(response_model.__semantic_slot_bindings__)
            self.assertEqual(set(bindings), {item["wire_id"] for item in assigned})
            successful_ids.extend(spec.slot_id for spec in bindings.values())
            payload = {
                "slots": {
                    wire_id: {
                        "meaning": f"meaning for {spec.slot_id}",
                        "status": "sufficient",
                        "missing_context": [],
                    }
                    for wire_id, spec in bindings.items()
                },
            }
            if "stereotype" in response_model.model_fields:
                stereotype_batches.append([
                    spec.slot_id for spec in bindings.values()
                ])
                payload["stereotype"] = "Validator"
            return response_model.model_validate(payload)

        result = asyncio.run(_run_bounded_frame_summary(
            profile=profile,
            slot_specs=summary_specs,
            strategy_name="framework",
            common={"raw_code_body": "same complete source", "module_intent": "same intent"},
            invoke=invoke,
        ))

        self.assertTrue(result.summary)
        self.assertEqual({spec.slot_id for spec in summary_specs}, set(successful_ids))
        self.assertEqual(len(successful_ids), len(set(successful_ids)))
        self.assertEqual(
            {("same complete source", "same intent")},
            set(observed_common),
        )
        self.assertEqual(1, len(stereotype_batches))

    def test_non_owner_summary_schema_forbids_stereotype(self):
        _profile, specs = build_code_semantic_plan(_routine(), _context())
        summary_specs = semantic_concern_slots(specs)["summary"]
        non_owner = tuple(
            spec for spec in summary_specs
            if not (spec.family == "FUNCTION" and spec.role == "RESPONSIBILITY")
        )[:1]
        model = frame_summary_response_model(
            "framework", non_owner, include_stereotype=False,
        )
        payload = {
            "slots": {
                semantic_slot_wire_ids(non_owner)[0]: {
                    "meaning": "직접 동작을 수행한다",
                    "status": "sufficient",
                    "missing_context": [],
                },
            },
            "stereotype": "Validator",
        }
        with self.assertRaises(ValidationError):
            model.model_validate(payload)

    def test_status_cross_field_contract_is_provider_visible(self):
        _profile, specs = build_code_semantic_plan(_routine(), _context())
        model = semantic_frame_response_model(specs)
        wire_ids = semantic_slot_wire_ids(specs)
        invalid = {
            "slots": {
                wire_id: {
                    "meaning": "근거가 부족하지만 의미를 씀",
                    "status": "insufficient",
                    "missing_context": ["callee 계약"],
                }
                for spec, wire_id in zip(specs, wire_ids, strict=True)
            },
        }
        with self.assertRaises(ValidationError):
            model.model_validate(invalid)

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
            model = semantic_frame_response_model(group)
            wire_ids = semantic_slot_wire_ids(group)
            output = model.model_validate({"slots": {
                wire_id: {
                    "meaning": f"{spec.family}.{spec.role}",
                    "status": "sufficient",
                    "missing_context": [],
                }
                for spec, wire_id in zip(group, wire_ids, strict=True)
            }})
            fragments.append(normalize_semantic_frame_response(output, profile, group))
        merged = merge_semantic_frames(
            *fragments,
            required_slot_ids=(spec.slot_id for spec in specs),
        )
        self.assertEqual({spec.slot_id for spec in specs}, set(merged.slots))

    def test_legacy_summary_and_questions_are_frame_projections(self):
        profile, specs = build_code_semantic_plan(_routine(), _context())
        model = frame_summary_response_model("framework", specs)
        responsibility = next(
            spec for spec in specs
            if spec.family == "FUNCTION" and spec.role == "RESPONSIBILITY"
        )
        payload = {
            "slots": {
                wire_id: ({
                    "meaning": "주문 저장 책임을 수행한다",
                    "status": "partial",
                    "missing_context": ["reserve의 실패 반환 계약이 필요함"],
                } if spec == responsibility else {
                    "meaning": f"{spec.family}.{spec.role}을 수행한다",
                    "status": "sufficient",
                    "missing_context": [],
                })
                for spec, wire_id in zip(
                    specs, semantic_slot_wire_ids(specs), strict=True,
                )
            },
            "stereotype": "ApplicationService",
        }
        parsed = model.model_validate(payload)
        summary = normalize_frame_summary_response(parsed, profile, specs)
        self.assertIsNotNone(summary.semantic_frame)
        self.assertEqual("partial", summary.evidence_status)
        self.assertIn("주문 저장 책임", summary.summary)
        self.assertEqual(
            "reserve의 실패 반환 계약이 필요함",
            summary.questions[0].text,
        )


if __name__ == "__main__":
    unittest.main()
