"""The frame wire owns prose only; parser/linker metadata remains immutable."""
from __future__ import annotations

import asyncio
import hashlib
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
    mark_semantic_frame_insufficient,
    merge_semantic_frames,
    normalize_semantic_frame_response,
    profile_prompt_json,
    semantic_frame_response_model,
    semantic_slot_wire_ids,
    slot_specs_json,
    target_digest,
)
from pipeline.models.semantic_frame_projection import project_summary
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
        guarded_rule_refs = [
            ref
            for spec in _specs
            if spec.family == "FUNCTION" and spec.role == "GUARDS_AND_BRANCHES"
            for ref in spec.evidence_refs
            if profile.evidence[ref].kind == "RULE"
        ]
        expected_guard_refs = {
            ref for ref, fact in profile.evidence.items()
            if fact.kind == "RULE"
            and fact.attributes["role"] in {"decision", "complement", "handler"}
        }
        self.assertEqual(expected_guard_refs, set(guarded_rule_refs))
        self.assertEqual(len(expected_guard_refs), len(guarded_rule_refs))
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
            call_lines=_context().extra["ast_call_lines"],
        )
        profile, specs = build_code_semantic_plan(
            _routine(), _context(), inventory=inventory,
        )

        roles = {(spec.family, spec.role) for spec in specs}
        self.assertIn(("FUNCTION", "RESPONSIBILITY"), roles)
        self.assertIn(("FUNCTION", "INPUT_ROLE"), roles)
        self.assertIn(("FUNCTION", "GUARDS_AND_BRANCHES"), roles)
        self.assertIn(("FUNCTION", "PROCESSING_EFFECTS"), roles)
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

    def test_semantic_leaf_has_no_arbitrary_character_or_diagnostic_cap(self):
        profile, specs = build_code_semantic_plan(_routine(), _context())
        model = semantic_frame_response_model(specs)
        wire_ids = semantic_slot_wire_ids(specs)
        long_meaning = "근거문장" * 400
        missing = [f"근거 의무 {index}" for index in range(24)]
        payload = {"slots": {
            wire_id: ({
                "meaning": long_meaning,
                "status": "partial",
                "missing_context": missing,
            } if index == 0 else {
                "meaning": f"{spec.family}.{spec.role}",
                "status": "sufficient",
                "missing_context": [],
            })
            for index, (spec, wire_id) in enumerate(zip(
                specs, wire_ids, strict=True,
            ))
        }}

        schema_text = json.dumps(model.model_json_schema(), sort_keys=True)
        self.assertNotIn('"maxLength": 600', schema_text)
        self.assertNotIn('"maxItems": 8', schema_text)
        frame = normalize_semantic_frame_response(
            model.model_validate(payload), profile, specs,
        )
        slot = frame.slots[specs[0].slot_id]
        self.assertEqual(long_meaning, slot.meaning)
        self.assertEqual(tuple(missing), slot.missing_context)

    def test_projection_and_rejection_preserve_every_distinct_diagnostic(self):
        profile, specs = build_code_semantic_plan(_routine(), _context())
        model = semantic_frame_response_model(specs)
        wire_ids = semantic_slot_wire_ids(specs)
        missing = [f"근거 의무 {index}" for index in range(24)]
        parsed = model.model_validate({"slots": {
            wire_id: ({
                "meaning": "확정된 일부 의미",
                "status": "partial",
                "missing_context": missing,
            } if index == 0 else {
                "meaning": f"{spec.family}.{spec.role}",
                "status": "sufficient",
                "missing_context": [],
            })
            for index, (spec, wire_id) in enumerate(zip(
                specs, wire_ids, strict=True,
            ))
        }})
        frame = normalize_semantic_frame_response(parsed, profile, specs)

        projected = project_summary(frame, stereotype="ApplicationService")
        self.assertEqual(missing, projected.missing_context)
        self.assertEqual(missing, [question.text for question in projected.questions])

        rejected = mark_semantic_frame_insufficient(frame, missing)
        self.assertTrue(all(
            slot.missing_context == tuple(missing)
            for slot in rejected.slots.values()
        ))

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
        statement_refs = {
            ref for ref, fact in profile.evidence.items()
            if fact.kind == "STATEMENT" and fact.attributes["type"] == "ASSIGNMENT"
        }
        processing_refs = [
            ref
            for spec in summary_specs
            if spec.family == "FUNCTION" and spec.role == "PROCESSING_EFFECTS"
            for ref in spec.evidence_refs
            if ref in statement_refs
        ]

        self.assertEqual(assigned_refs, set(projected["evidence"]))
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
        context.extra["ast_call_lines"] = tuple(
            (line, f"call_{line}") for line in range(1, 71)
        )
        profile, specs = build_code_semantic_plan(_routine(), context)
        summary_specs = semantic_concern_slots(specs)["summary"]

        batches = _summary_slot_batches(summary_specs)
        assigned = [spec.slot_id for batch in batches for spec in batch]

        call_refs = {
            ref for ref, fact in profile.evidence.items()
            if fact.kind == "CALL" and fact.start_line > 0
        }
        processing_refs = [
            ref
            for spec in summary_specs
            if spec.family == "FUNCTION" and spec.role == "PROCESSING_EFFECTS"
            for ref in spec.evidence_refs
            if ref in call_refs
        ]

        self.assertGreater(len(summary_specs), 5)
        self.assertEqual({spec.slot_id for spec in summary_specs}, set(assigned))
        self.assertEqual(len(assigned), len(set(assigned)))
        self.assertEqual(call_refs, set(processing_refs))
        self.assertEqual(len(call_refs), len(processing_refs))
        self.assertEqual(70, len(call_refs))
        self.assertFalse(any(spec.family == "CALL" for spec in specs))

    def test_each_model_call_receives_exact_assigned_evidence_and_source_closure(self):
        profile, specs = build_code_semantic_plan(_routine(), _context())
        summary_specs = semantic_concern_slots(specs)["summary"]
        source_lines = [f"source line {line}" for line in range(10, 17)]
        source_lines[3] = r'''call("\\t", 'quoted');'''
        source = "\n".join(source_lines)
        observed_ids: list[str] = []

        async def invoke(values, response_model, _batch_label):
            bindings = dict(response_model.__semantic_slot_bindings__)
            assigned_specs = tuple(bindings.values())
            assigned_refs = {
                ref
                for spec in assigned_specs
                for ref in (spec.target_ref, *spec.evidence_refs)
            }
            prompt_profile = json.loads(values["structural_profile"])
            self.assertEqual(assigned_refs, set(prompt_profile["evidence"]))

            packet = json.loads(values["code_body"])
            self.assertEqual("source-closure/v1", packet["schema"])
            self.assertEqual(
                hashlib.sha256(source.encode("utf-8")).hexdigest(),
                packet["target_sha256"],
            )
            owns_target = any(
                spec.family == "FUNCTION" and spec.role == "RESPONSIBILITY"
                for spec in assigned_specs
            )
            if owns_target:
                expected_lines = list(range(10, 17))
            else:
                expected_lines = sorted({
                    line
                    for ref in assigned_refs
                    for fact in (profile.evidence[ref],)
                    if fact.kind != "TARGET" and fact.start_line > 0
                    for line in range(fact.start_line, fact.end_line + 1)
                    if 10 <= line <= 16
                })
            self.assertEqual(expected_lines, [item["line"] for item in packet["lines"]])
            self.assertEqual(
                [source_lines[line - 10] for line in expected_lines],
                [item["text"] for item in packet["lines"]],
            )
            self.assertEqual(
                "(없음: 파생 prose는 의미 근거로 전달하지 않음)",
                values["module_intent"],
            )
            observed_ids.extend(spec.slot_id for spec in assigned_specs)
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
                payload["stereotype"] = "Validator"
            return response_model.model_validate(payload)

        result = asyncio.run(_run_bounded_frame_summary(
            profile=profile,
            slot_specs=summary_specs,
            strategy_name="framework",
            common={"raw_code_body": source, "module_intent": "derived intent"},
            invoke=invoke,
            max_input_tokens=1,
        ))

        self.assertTrue(result.summary)
        self.assertEqual({spec.slot_id for spec in summary_specs}, set(observed_ids))
        self.assertEqual(len(summary_specs), len(observed_ids))

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
        context.extra["ast_call_lines"] = ()
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

        self.assertEqual(
            {"RESPONSIBILITY", "INPUT_ROLE", "RESULT"}, set(roles),
        )
        self.assertNotIn("GUARDS_AND_BRANCHES", roles)
        self.assertNotIn("PROCESSING_EFFECTS", roles)
        self.assertLessEqual(len(roles), 5)

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
        guard_context.extra["ast_call_lines"] = ()
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
        self.assertIn("GUARDS_AND_BRANCHES", guard_roles)
        self.assertNotIn("PROCESSING_EFFECTS", guard_roles)

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
        effect_context.extra["ast_call_lines"] = ()

        _profile, effect_specs = build_code_semantic_plan(fn, effect_context)
        effect_roles = {spec.role for spec in effect_specs if spec.family == "FUNCTION"}
        self.assertNotIn("GUARDS_AND_BRANCHES", effect_roles)
        self.assertIn("PROCESSING_EFFECTS", effect_roles)

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
            call_lines=context.extra["ast_call_lines"],
        )
        profile, specs = build_code_semantic_plan(
            _routine(), context, inventory=inventory,
        )
        summary_specs = semantic_concern_slots(specs)["summary"]
        model = frame_summary_response_model("framework", summary_specs)
        meanings = {
            "RESPONSIBILITY": "1 책임",
            "INPUT_ROLE": "2 입력",
            "GUARDS_AND_BRANCHES": "3 조건",
            "PROCESSING_EFFECTS": "4 처리",
            "RESULT": "5 결과",
        }
        payload = {
            "slots": {
                wire_id: {
                    "meaning": meanings[spec.role],
                    "status": "sufficient",
                    "missing_context": [],
                }
                for spec, wire_id in zip(
                    summary_specs,
                    semantic_slot_wire_ids(summary_specs),
                    strict=True,
                )
            },
            "stereotype": "ApplicationService",
        }

        summary = normalize_frame_summary_response(
            model.model_validate(payload), profile, summary_specs,
        )

        self.assertEqual(
            ["1 책임", "2 입력", "3 조건", "4 처리", "5 결과"],
            summary.summary.splitlines(),
        )

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
        context.extra["ast_call_lines"] = ()
        context.extra["data_object_usages"] = ()
        context.callees = ()
        profile, specs = build_code_semantic_plan(_routine(), context)
        summary_specs = semantic_concern_slots(specs)["summary"]
        model = frame_summary_response_model("framework", summary_specs)

        def meaning(spec):
            if spec.role != "PROCESSING_EFFECTS":
                return f"{spec.role}"
            line = min(
                profile.evidence[ref].start_line
                for ref in spec.evidence_refs
                if profile.evidence[ref].kind != "TARGET"
                and profile.evidence[ref].start_line > 0
            )
            return f"processing line {line}"

        parsed = model.model_validate({
            "slots": {
                wire_id: {
                    "meaning": meaning(spec),
                    "status": "sufficient",
                    "missing_context": [],
                }
                for spec, wire_id in zip(
                    summary_specs, semantic_slot_wire_ids(summary_specs), strict=True,
                )
            },
            "stereotype": "Validator",
        })
        projected = normalize_frame_summary_response(parsed, profile, summary_specs)

        self.assertEqual(
            ["processing line 11", "processing line 12"],
            [line for line in projected.summary.splitlines() if line.startswith("processing")],
        )

    def test_summary_execution_batches_preserve_unbounded_obligation_count(self):
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

        batches = _summary_slot_batches(primary)
        assigned = [spec.slot_id for batch in batches for spec in batch]

        self.assertEqual({spec.slot_id for spec in primary}, set(assigned))
        self.assertEqual(len(primary), len(assigned))
        self.assertEqual(len(assigned), len(set(assigned)))

    def test_summary_deadline_splits_schema_without_repeating_or_losing_slots(self):
        profile, specs = build_code_semantic_plan(_routine(), _context())
        summary_specs = semantic_concern_slots(specs)["summary"]
        source = "\n".join(f"source line {line}" for line in range(10, 17))
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
            common={"raw_code_body": source, "module_intent": "derived intent"},
            invoke=invoke,
        ))

        self.assertTrue(result.summary)
        self.assertEqual(
            {spec.slot_id for spec in summary_specs},
            set(successful_ids),
        )
        self.assertEqual(len(successful_ids), len(set(successful_ids)))
        self.assertTrue(observed_common)
        self.assertIn(source, {body for body, _intent in observed_common})
        self.assertTrue(all(
            intent == "(없음: 파생 prose는 의미 근거로 전달하지 않음)"
            for _body, intent in observed_common
        ))
        self.assertTrue(all(
            all(line in source.splitlines() for line in body.splitlines())
            for body, _intent in observed_common
        ))
        self.assertEqual(1, len(stereotype_batches))
        self.assertTrue(any(
            spec.family == "FUNCTION" and spec.role == "RESPONSIBILITY"
            for spec in summary_specs
            if spec.slot_id in stereotype_batches[0]
        ))

    def test_summary_repetition_splits_without_losing_shared_context_or_slots(self):
        profile, specs = build_code_semantic_plan(_routine(), _context())
        summary_specs = semantic_concern_slots(specs)["summary"]
        source = "\n".join(f"source line {line}" for line in range(10, 17))
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
            common={"raw_code_body": source, "module_intent": "derived intent"},
            invoke=invoke,
        ))

        self.assertTrue(result.summary)
        self.assertEqual({spec.slot_id for spec in summary_specs}, set(successful_ids))
        self.assertEqual(len(successful_ids), len(set(successful_ids)))
        self.assertIn(source, {body for body, _intent in observed_common})
        self.assertTrue(all(
            intent == "(없음: 파생 prose는 의미 근거로 전달하지 않음)"
            for _body, intent in observed_common
        ))
        self.assertTrue(all(
            all(line in source.splitlines() for line in body.splitlines())
            for body, _intent in observed_common
        ))
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
