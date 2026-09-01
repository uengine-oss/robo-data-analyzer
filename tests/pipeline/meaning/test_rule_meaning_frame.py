from __future__ import annotations

import unittest

from pydantic import ValidationError

from pipeline.models.analysis_context import ReferencedSymbolContext
from pipeline.meaning.rule.meaning_frame import (
    batch_rule_meaning_response_model,
    build_rule_meaning_packet,
    prepare_rule_meaning_assignment,
    project_batch_rule_meanings,
    project_rule_meanings,
    rule_meaning_response_model,
)


_EVIDENCE = {
    "conditions": {"C17": 'ctx->gc_usim_yn != "Y"'},
    "effects": {
        "E1": {
            "op": "call",
            "target": "c520_mms_psbl_yn_check",
            "value": None,
            "from_source": None,
            "occurrence_id": "private-occurrence",
            "call_source": "c520_mms_psbl_yn_check(ctx)",
            "argument_sources": ["ctx"],
            "receiver_source": None,
            "callee_id": "callee-private-id",
        }
    },
    "rows": {
        "R17": {
            "role": "decision",
            "when": ["C17"],
            "narrative_focus": ["C17"],
            "direct": ["E1"],
            "source": "c520_mms_psbl_yn_check(ctx);",
        }
    },
}


class RuleMeaningFrameTest(unittest.TestCase):
    def test_provider_schema_requires_every_exact_atom_once(self) -> None:
        model = rule_meaning_response_model(("C1",), ("E1",), ("R1",))
        self.assertEqual(["conditions", "effects", "rows"], list(model.model_fields))
        self.assertEqual(
            ["conditions", "effects", "rows"],
            list(model.model_json_schema()["properties"]),
        )
        valid = {
            "conditions": {"C1": "USIM 상태가 Y가 아니다"},
            "effects": {"E1": "MMS 가능 여부를 확인한다"},
            "rows": {"R1": "business_rule"},
        }
        model.model_validate(valid)
        with self.assertRaises(ValidationError):
            model.model_validate({**valid, "conditions": {"C1": "복창", "C2": "추가"}})
        with self.assertRaises(ValidationError):
            model.model_validate({
                **valid,
                "effects": {
                    **valid["effects"],
                    "E2": valid["effects"]["E1"],
                },
            })
        with self.assertRaises(ValidationError):
            model.model_validate({**valid, "rows": {}})

    def test_prompt_packet_omits_private_ids_and_attaches_consumed_context(self) -> None:
        packet = build_rule_meaning_packet(
            _EVIDENCE,
            numbered_source=(
                '1135 | if (ctx->gc_usim_yn != "Y") {\n'
                "1138 |   c520_mms_psbl_yn_check(ctx);\n"
                "1139 | }"
            ),
            referenced_symbols=(
                ReferencedSymbolContext(
                    id="private-symbol-id",
                    name="gc_usim_yn",
                    kind="field",
                    data_type="char[2]",
                    declared_value='"N"',
                ),
                ReferencedSymbolContext(
                    id="recoverable-name-only",
                    name="ctx",
                    kind="parameter",
                ),
            ),
            callee_outcomes_by_id={
                "callee-private-id": {"meaning": "가입 상태를 바탕으로 MMS 가능 여부를 반환한다"},
            },
            api_contracts=(
                {"name": "c520_mms_psbl_yn_check", "return_role": "가능 여부"},
            ),
        )
        rendered = str(packet)
        for forbidden in (
            "private-occurrence",
            "private-symbol-id",
            "callee-private-id",
            "narrative_focus",
        ):
            self.assertNotIn(forbidden, rendered)
        self.assertEqual([{
            "name": "gc_usim_yn",
            "kind": "field",
            "type": "char[2]",
            "declared_value": '"N"',
        }], packet["used_symbols"])
        effect = packet["effects"]["E1"]
        self.assertEqual(
            "c520_mms_psbl_yn_check(ctx)",
            effect["source"],
        )
        self.assertNotIn("receiver", effect)
        self.assertNotIn("arguments", effect)
        self.assertEqual(
            "가입 상태를 바탕으로 MMS 가능 여부를 반환한다",
            effect["callee_outcome"],
        )
        self.assertEqual({"return_role": "가능 여부"}, effect["api_contract"])
        self.assertEqual(
            {"kind": "decision", "when": ["C1"], "effects": ["E1"]},
            packet["rows"]["R1"],
        )

    def test_rule_table_effect_gets_only_the_existing_table_meaning(self) -> None:
        evidence = {
            "conditions": {"C1": "ready"},
            "effects": {
                "E1": {
                    "op": "update",
                    "target": "db:orders",
                    "value": None,
                    "from_source": None,
                },
            },
            "rows": {
                "R1": {
                    "role": "decision",
                    "when": ["C1"],
                    "narrative_focus": ["C1"],
                    "direct": ["E1"],
                    "source": "UPDATE orders SET status = 'DONE'",
                },
            },
        }
        packet = build_rule_meaning_packet(
            evidence,
            numbered_source="1 | UPDATE orders SET status = 'DONE';",
            table_descriptions={
                "db:orders": {
                    "name": "orders",
                    "description": "주문 상태",
                    "columns": ["order_id", "status", "unused_note"],
                    "sample_rows": [{"order_id": 1, "status": "READY"}],
                },
            },
        )

        effect = packet["effects"]["E1"]
        self.assertEqual("주문 상태", effect["table_meaning"])
        self.assertNotIn("table_context", effect)
        rendered = str(packet)
        self.assertNotIn("unused_note", rendered)
        self.assertNotIn("sample_rows", rendered)

    def test_removed_effect_metadata_is_rejected(self) -> None:
        evidence = {
            **_EVIDENCE,
            "effects": {
                "E1": {
                    **_EVIDENCE["effects"]["E1"],
                    "resolution_status": "internal",
                },
            },
        }
        with self.assertRaises(ValidationError):
            build_rule_meaning_packet(
                evidence,
                numbered_source="1138 | c520_mms_psbl_yn_check(ctx);",
            )

    def test_rule_structure_and_display_meaning_are_kept_separate(self) -> None:
        model = rule_meaning_response_model(("C1",), ("E1",), ("R1",))
        output = model.model_validate({
            "conditions": {"C1": "USIM 상태가 Y가 아니다"},
            "effects": {"E1": "MMS 가능 여부를 확인한다"},
            "rows": {"R1": "business_rule"},
        })
        [row] = project_rule_meanings(output, _EVIDENCE)
        self.assertEqual("R17", row.row_key)
        self.assertEqual(17, row.anchor_line)
        self.assertEqual("sufficient", row.status)
        self.assertEqual("business_rule", row.disposition)
        self.assertEqual("USIM 상태가 Y가 아니다", row.condition_description)
        self.assertEqual(("MMS 가능 여부를 확인한다",), row.effect_descriptions)

    def test_insufficient_atom_never_becomes_an_accepted_example(self) -> None:
        model = rule_meaning_response_model(("C1",), ("E1",), ("R1",))
        output = model.model_validate({
            "conditions": {"C1": "USIM 상태가 Y가 아니다"},
            "effects": {"E1": ""},
            "rows": {"R1": "business_rule"},
        })
        [row] = project_rule_meanings(output, _EVIDENCE)
        self.assertEqual("insufficient", row.status)
        self.assertEqual((), row.effect_descriptions)

    def test_implementation_detail_is_classified_without_projecting_python(self) -> None:
        model = rule_meaning_response_model(("C1",), ("E1",), ("R1",))
        output = model.model_validate({
            "conditions": {"C1": "USIM 상태가 Y가 아니다"},
            "effects": {"E1": "진단 로그를 남긴다"},
            "rows": {"R1": "implementation_detail"},
        })
        [row] = project_rule_meanings(output, _EVIDENCE)
        self.assertEqual("implementation_detail", row.disposition)
        self.assertEqual("not_business", row.status)
        self.assertEqual((), row.effect_descriptions)

    def test_parent_only_nested_control_is_removed_before_the_model_boundary(self) -> None:
        evidence = {
            "conditions": {"C1": "outer", "C2": "inner"},
            "effects": {"E1": {
                "op": "ret", "target": None, "value": "ok", "from_source": True,
            }},
            "rows": {
                "R1": {
                    "role": "decision", "when": ["C1"], "direct": [],
                    "nested_control": True,
                },
                "R2": {
                    "role": "decision", "when": ["C1", "C2"], "direct": ["E1"],
                },
            },
        }

        assignment = prepare_rule_meaning_assignment(evidence)

        self.assertEqual({"R2"}, set(assignment.model_evidence["rows"]))
        self.assertEqual({"C1", "C2"}, set(assignment.model_evidence["conditions"]))
        self.assertEqual({"E1"}, set(assignment.model_evidence["effects"]))
        self.assertEqual(1, len(assignment.deterministic_projections))
        parent = assignment.deterministic_projections[0]
        self.assertEqual("R1", parent.row_key)
        self.assertEqual("implementation_detail", parent.disposition)
        self.assertEqual("not_business", parent.status)

    def test_any_row_without_a_direct_effect_is_removed_before_the_model_boundary(self) -> None:
        evidence = {
            "conditions": {"C1": "condition"},
            "effects": {},
            "rows": {
                "R840": {
                    "role": "decision",
                    "when": ["C1"],
                    "direct": [],
                    "nested_control": False,
                },
            },
        }

        assignment = prepare_rule_meaning_assignment(evidence)

        self.assertEqual({}, assignment.model_evidence["rows"])
        self.assertEqual({}, assignment.model_evidence["conditions"])
        self.assertEqual({}, assignment.model_evidence["effects"])
        [projection] = assignment.deterministic_projections
        self.assertEqual("R840", projection.row_key)
        self.assertEqual("implementation_detail", projection.disposition)
        self.assertEqual("not_business", projection.status)

    def test_dbms_batch_uses_exact_node_maps_without_echo_fields(self) -> None:
        assignments = {
            "N1": _EVIDENCE,
            "N2": _EVIDENCE,
        }
        model = batch_rule_meaning_response_model(assignments)
        node = {
            "conditions": {"C1": "USIM 상태가 Y가 아니다"},
            "effects": {"E1": "MMS 가능 여부를 확인한다"},
            "rows": {"R1": "business_rule"},
        }
        output = model.model_validate({"nodes": {
            "N1": node,
            "N2": node,
        }})
        projected = project_batch_rule_meanings(output, assignments)
        self.assertEqual(tuple(assignments), tuple(projected))
        self.assertTrue(all(rows[0].status == "sufficient" for rows in projected.values()))
        with self.assertRaises(ValidationError):
            model.model_validate({"nodes": {"N1": node}})

    def test_wire_keys_and_schema_do_not_expose_internal_coordinates_or_hashes(self) -> None:
        packet = build_rule_meaning_packet(
            _EVIDENCE,
            numbered_source="17 | c520_mms_psbl_yn_check(ctx);",
        )
        rendered = str(packet)
        self.assertNotIn("C17", rendered)
        self.assertNotIn("R17", rendered)
        model = rule_meaning_response_model(
            packet["conditions"], packet["effects"], packet["rows"],
        )
        schema = model.model_json_schema()
        self.assertNotIn("title", str(schema))
        self.assertNotRegex(str(schema), r"[0-9a-f]{12}")
        with self.assertRaises(ValueError):
            rule_meaning_response_model(("C17",), ("E1",), ("R17",))


if __name__ == "__main__":
    unittest.main()
