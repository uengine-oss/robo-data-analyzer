from __future__ import annotations

import ast
import unittest

from pydantic import ValidationError

from pipeline.models.analysis_context import ReferencedSymbolContext
from pipeline.models.rule_meaning_frame import (
    batch_rule_meaning_response_model,
    build_rule_meaning_packet,
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
        model = rule_meaning_response_model(("C17",), ("E1",), ("R17",))
        self.assertEqual(["effects", "rows"], list(model.model_fields))
        self.assertEqual(
            ["effects", "rows"],
            list(model.model_json_schema()["properties"]),
        )
        valid = {
            "effects": {"E1": "MMS 가능 여부를 확인한다"},
            "rows": {"R17": "business_rule"},
        }
        model.model_validate(valid)
        with self.assertRaises(ValidationError):
            model.model_validate({**valid, "conditions": {"C17": "복창"}})
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
        self.assertEqual(["ctx"], effect["arguments"])
        self.assertEqual(
            "가입 상태를 바탕으로 MMS 가능 여부를 반환한다",
            effect["callee_outcome"],
        )
        self.assertEqual({"return_role": "가능 여부"}, effect["api_contract"])
        self.assertEqual(
            {"kind": "decision", "when": ["C17"], "effects": ["E1"]},
            packet["rows"]["R17"],
        )

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

    def test_rule_and_example_are_one_deterministic_python_projection(self) -> None:
        model = rule_meaning_response_model(("C17",), ("E1",), ("R17",))
        output = model.model_validate({
            "effects": {"E1": "MMS 가능 여부를 확인한다"},
            "rows": {"R17": "business_rule"},
        })
        [row] = project_rule_meanings(output, _EVIDENCE)
        self.assertEqual("R17", row.row_key)
        self.assertEqual(17, row.anchor_line)
        self.assertEqual("sufficient", row.status)
        self.assertEqual("business_rule", row.disposition)
        for effect in row.effects:
            ast.parse(effect, mode="eval")

    def test_insufficient_atom_never_becomes_an_accepted_example(self) -> None:
        model = rule_meaning_response_model(("C17",), ("E1",), ("R17",))
        output = model.model_validate({
            "effects": {"E1": ""},
            "rows": {"R17": "business_rule"},
        })
        [row] = project_rule_meanings(output, _EVIDENCE)
        self.assertEqual("insufficient", row.status)
        self.assertEqual((), row.effects)

    def test_implementation_detail_is_classified_without_projecting_python(self) -> None:
        model = rule_meaning_response_model(("C17",), ("E1",), ("R17",))
        output = model.model_validate({
            "effects": {"E1": "진단 로그를 남긴다"},
            "rows": {"R17": "implementation_detail"},
        })
        [row] = project_rule_meanings(output, _EVIDENCE)
        self.assertEqual("implementation_detail", row.disposition)
        self.assertEqual("not_business", row.status)
        self.assertEqual((), row.effects)

    def test_dbms_batch_uses_exact_node_maps_without_echo_fields(self) -> None:
        assignments = {
            "1135-1139": _EVIDENCE,
            "1200-1205#stable": _EVIDENCE,
        }
        model = batch_rule_meaning_response_model(assignments)
        node = {
            "effects": {"E1": "MMS 가능 여부를 확인한다"},
            "rows": {"R17": "business_rule"},
        }
        output = model.model_validate({"nodes": {
            "1135-1139": node,
            "1200-1205#stable": node,
        }})
        projected = project_batch_rule_meanings(output, assignments)
        self.assertEqual(tuple(assignments), tuple(projected))
        self.assertTrue(all(rows[0].status == "sufficient" for rows in projected.values()))
        with self.assertRaises(ValidationError):
            model.model_validate({"nodes": {"1135-1139": node}})


if __name__ == "__main__":
    unittest.main()
