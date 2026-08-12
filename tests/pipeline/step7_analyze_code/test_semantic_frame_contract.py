"""The frame wire owns prose only; parser/linker metadata remains immutable."""
from __future__ import annotations

import unittest

from pydantic import ValidationError

from graph.schema.graph_nodes import CodeNode
from pipeline.models.analysis_context import (
    AnalysisContext,
    ControlSubtreeNode,
    ReferencedSymbolContext,
    StatementFact,
)
from pipeline.models.data_object_usage import DataObjectUsageEvidence
from pipeline.models.semantic_frame import (
    evidence_id,
    normalize_semantic_frame_response,
    semantic_frame_response_model,
    target_digest,
    merge_semantic_frames,
)
from pipeline.models.summary_response_schema import (
    frame_summary_response_model,
    normalize_frame_summary_response,
)
from pipeline.quality_gates.branch_inventory import build_inventory
from pipeline.steps.step7_analyze_code.semantic_frame_planner import (
    build_code_semantic_plan,
    semantic_concern_slots,
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
        self.assertIn(("FUNCTION", "ORDERED_FLOW"), roles)
        self.assertIn(("DECISION", "ROLE"), roles)
        self.assertIn(("DECISION", "TRUE_OUTCOME"), roles)
        self.assertIn(("CALL", "PURPOSE"), roles)
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

    def test_wire_requires_every_preassigned_slot_and_binds_metadata_outside_model(self):
        profile, specs = build_code_semantic_plan(_routine(), _context())
        model = semantic_frame_response_model(specs)
        payload = {
            "slots": {
                spec.slot_id: {
                    "meaning": f"{spec.family}.{spec.role} 의미",
                    "status": "sufficient",
                    "missing_context": [],
                }
                for spec in specs
            },
        }
        parsed = model.model_validate(payload)
        frame = normalize_semantic_frame_response(parsed, profile, specs)

        self.assertEqual("semantic-frame/v1", frame.schema_version)
        self.assertEqual(set(payload["slots"]), set(frame.slots))
        for spec in specs:
            self.assertEqual(spec.evidence_refs, frame.slots[spec.slot_id].evidence_refs)

        missing = {"slots": dict(payload["slots"])}
        missing["slots"].pop(specs[0].slot_id)
        with self.assertRaises(ValidationError):
            model.model_validate(missing)

        extra = {"slots": dict(payload["slots"])}
        extra["slots"]["SL:000000000000:CALL:PURPOSE:1"] = {
            "meaning": "창작 slot",
            "status": "sufficient",
            "missing_context": [],
        }
        with self.assertRaises(ValidationError):
            model.model_validate(extra)

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
        decision = next(
            spec for spec in specs
            if spec.family == "DECISION" and spec.role == "TRUE_OUTCOME"
        )
        statement_facts = [
            profile.evidence[ref] for ref in decision.evidence_refs
            if profile.evidence[ref].kind == "STATEMENT"
        ]
        self.assertEqual([11], [fact.start_line for fact in statement_facts])

    def test_rule_rows_bound_the_slots_required_from_the_rule_concern(self):
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
        allowed_rows = (all_rows[0],)
        profile, specs = build_code_semantic_plan(
            _routine(), context, inventory=inventory, rule_rows=allowed_rows,
        )
        rule_slots = semantic_concern_slots(specs)["rules"]
        anchors = {
            int(profile.evidence[ref].attributes["anchor_line"])
            for spec in rule_slots
            for ref in spec.evidence_refs
            if profile.evidence[ref].kind == "RULE"
        }

        self.assertEqual(set(allowed_rows), anchors)
        self.assertTrue(set(all_rows) - anchors)

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

        _profile, specs = build_code_semantic_plan(
            _routine(), context, inventory=inventory, rule_rows=(handler_row,),
        )

        self.assertIn(
            ("EXCEPTION", "PROPAGATION"),
            {(spec.family, spec.role) for spec in specs},
        )

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

    def test_status_cross_field_contract_is_provider_visible(self):
        _profile, specs = build_code_semantic_plan(_routine(), _context())
        model = semantic_frame_response_model(specs)
        invalid = {
            "slots": {
                spec.slot_id: {
                    "meaning": "근거가 부족하지만 의미를 씀",
                    "status": "insufficient",
                    "missing_context": ["callee 계약"],
                }
                for spec in specs
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
            output = model.model_validate({"slots": {
                spec.slot_id: {
                    "meaning": f"{spec.family}.{spec.role}",
                    "status": "sufficient",
                    "missing_context": [],
                }
                for spec in group
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
                spec.slot_id: ({
                    "meaning": "주문 저장 책임을 수행한다",
                    "status": "partial",
                    "missing_context": ["reserve의 실패 반환 계약이 필요함"],
                } if spec == responsibility else {
                    "meaning": f"{spec.family}.{spec.role}을 수행한다",
                    "status": "sufficient",
                    "missing_context": [],
                })
                for spec in specs
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
