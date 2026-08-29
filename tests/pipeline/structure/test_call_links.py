from __future__ import annotations

import unittest

from graph.schema.graph_edges import Calls
from graph.schema.graph_nodes import CodeNode
from pipeline.structure.call_links import (
    CallLinksProjectionError,
    build_call_links_write_batch,
)


def _routine(stable_id: str) -> CodeNode:
    return CodeNode(
        id=stable_id,
        signature=f"{stable_id}()",
        file_path="selected.c",
        start_line=1,
        end_line=2,
        label="FUNCTION",
        name=stable_id,
    )


def _occurrence(
    *,
    call_id: str,
    ordinal: int,
    caller_id: str,
    resolution_status: str,
    callee_id: str = "",
) -> dict:
    internal = resolution_status == "internal"
    external = resolution_status == "external"
    return {
        "call_id": call_id,
        "ordinal": ordinal,
        "caller_id": caller_id,
        "resolution_status": resolution_status,
        "adapter_schema": "analyzer-c-semantic-adapter/v1",
        "outcome": "edge" if internal else "preserved",
        "reason": "" if internal else "definition_outside_upload",
        "scope": "internal" if internal else None,
        "callee_id": callee_id if internal else "",
        "external_identity_key": "" if internal else "c::callee",
        "declaration_fact_id": "definition-1" if internal else "",
        "candidate_fact_ids": ["definition-1"] if internal else [],
        "preprocessing_evidence_ids": [],
        "macro_fact_ids": [],
    }


class CallLinksProjectionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.caller = _routine("caller")
        self.callee = _routine("callee")

    def _build(self, **overrides):
        values = {
            "analysis_scope_id": "scope",
            "generation_id": "generation",
            "code_nodes": [self.caller, self.callee],
            "calls_edges": [Calls("caller", "callee")],
            "call_resolution_ledger": [
                _occurrence(
                    call_id="call-1",
                    ordinal=0,
                    caller_id="caller",
                    resolution_status="internal",
                    callee_id="callee",
                ),
            ],
        }
        values.update(overrides)
        return build_call_links_write_batch(**values)

    def test_repeated_internal_occurrences_create_one_product_pair(self) -> None:
        batch = self._build(
            calls_edges=[Calls("caller", "callee"), Calls("caller", "callee")],
            call_resolution_ledger=[
                _occurrence(
                    call_id="call-1",
                    ordinal=0,
                    caller_id="caller",
                    resolution_status="internal",
                    callee_id="callee",
                ),
                _occurrence(
                    call_id="call-2",
                    ordinal=1,
                    caller_id="caller",
                    resolution_status="internal",
                    callee_id="callee",
                ),
            ],
        )

        self.assertEqual("call_links", batch.stage_name)
        self.assertEqual(1, len(batch.relationship_create_operations))
        operation = batch.relationship_create_operations[0]
        self.assertEqual("CALLS", operation.identity.relationship_type)
        self.assertEqual(
            {
                "analysis_scope_id": "scope",
                "generation_id": "generation",
            },
            dict(operation.present_properties),
        )

    def test_external_occurrence_creates_no_product_relationship(self) -> None:
        batch = self._build(
            calls_edges=[],
            call_resolution_ledger=[
                _occurrence(
                    call_id="call-1",
                    ordinal=0,
                    caller_id="caller",
                    resolution_status="external",
                ),
            ],
        )

        self.assertEqual(0, len(batch.relationship_create_operations))
        self.assertEqual(0, len(batch.required_operation_key_set))

    def test_edge_missing_from_internal_ledger_projection_fails(self) -> None:
        with self.assertRaisesRegex(
            CallLinksProjectionError,
            "CALLS edges differ",
        ):
            self._build(calls_edges=[])

    def test_unexpected_edge_for_external_occurrence_fails(self) -> None:
        with self.assertRaisesRegex(
            CallLinksProjectionError,
            "CALLS edges differ",
        ):
            self._build(call_resolution_ledger=[
                _occurrence(
                    call_id="call-1",
                    ordinal=0,
                    caller_id="caller",
                    resolution_status="external",
                ),
            ])

    def test_internal_endpoint_must_be_a_user_routine(self) -> None:
        statement = CodeNode(
            id="callee",
            signature="callee",
            file_path="selected.c",
            start_line=2,
            end_line=2,
            label="CALL",
        )
        with self.assertRaisesRegex(
            CallLinksProjectionError,
            "not a verified user routine",
        ):
            self._build(code_nodes=[self.caller, statement])

    def test_duplicate_occurrence_identity_fails_before_projection(self) -> None:
        occurrence = _occurrence(
            call_id="call-1",
            ordinal=0,
            caller_id="caller",
            resolution_status="internal",
            callee_id="callee",
        )
        duplicate = dict(occurrence)
        duplicate["ordinal"] = 1
        with self.assertRaisesRegex(
            CallLinksProjectionError,
            "duplicate canonical call_id",
        ):
            self._build(call_resolution_ledger=[occurrence, duplicate])

    def test_recursion_flag_is_reconstructed_from_endpoints(self) -> None:
        recursive = _occurrence(
            call_id="call-1",
            ordinal=0,
            caller_id="caller",
            resolution_status="internal",
            callee_id="caller",
        )
        with self.assertRaisesRegex(
            CallLinksProjectionError,
            "recursion flag disagrees",
        ):
            self._build(
                code_nodes=[self.caller],
                calls_edges=[Calls("caller", "caller", recursive=False)],
                call_resolution_ledger=[recursive],
            )


if __name__ == "__main__":
    unittest.main()
