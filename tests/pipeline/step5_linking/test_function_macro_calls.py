from __future__ import annotations

import unittest

from graph.schema.graph_nodes import CodeNode
from pipeline.steps.step5_linking.link_calls.call_classifier import classify_call_sites


class FunctionMacroCallTests(unittest.TestCase):
    def test_missing_configured_trace_precedes_every_raw_source_classification(self) -> None:
        caller = _caller()
        call = _call(caller, "customer_hook")
        call["configured_preprocessing_required"] = True
        result = classify_call_sites(
            [call], [caller],
            preprocessing_contexts=[{
                "source_id": "sample.c",
                "version": "1.0.0",
                "status": "unresolved",
                "trust": "unresolved",
                "build": {
                    "status": "unresolved",
                    "unresolvedEvidenceIds": ["a" * 64],
                    "reasons": ["insufficient_compilation_database"],
                },
                "trace": {
                    "status": "unresolved",
                    "evidenceIds": ["b" * 64],
                    "reasons": ["insufficient_preprocessing_build_context"],
                },
            }],
        )

        self.assertEqual([], result.edges)
        self.assertEqual(1, result.unresolved_preprocessing)
        self.assertEqual([{
            "call_id": "trace-call",
            "outcome": "unresolved",
            "reason": "insufficient_preprocessing_build_context",
            "preprocessing_evidence_ids": ["a" * 64, "b" * 64],
        }], result.ledger)

    def test_declaration_alone_cannot_claim_macro_expansion(self) -> None:
        caller = _caller()
        result = classify_call_sites([_call(caller, "TRACE")], [caller], macro_facts=[{
            "fact_id": "macro-trace",
            "source_id": "sample.c",
            "macro_kind": "function",
            "terminal_name": "TRACE",
            "presence_status": "active",
        }])

        self.assertEqual([], result.edges)
        self.assertEqual(0, result.dropped_macro)
        self.assertEqual(1, result.unresolved_macro)
        self.assertEqual([{
            "call_id": "trace-call",
            "outcome": "unresolved",
            "reason": "insufficient_macro_expansion_state",
            "macro_fact_ids": ["macro-trace"],
        }], result.ledger)

    def test_conditional_definition_is_explicitly_unresolved(self) -> None:
        caller = _caller()
        result = classify_call_sites([_call(caller, "PFM_DBG")], [caller], macro_facts=[{
            "fact_id": "macro-pfm-dbg",
            "source_id": "sample.c",
            "macro_kind": "function",
            "terminal_name": "PFM_DBG",
            "presence_status": "conditional",
        }])

        self.assertEqual([], result.edges)
        self.assertEqual(1, result.unresolved_macro)
        self.assertEqual(
            "insufficient_conditional_macro_definition", result.ledger[0]["reason"],
        )

    def test_object_and_inactive_function_macro_do_not_suppress_call(self) -> None:
        caller = _caller()
        sites = [
            _call(caller, "CALLBACK", call_id="call-object"),
            _call(caller, "TRACE", call_id="call-inactive"),
            _call(caller, "customer_hook", call_id="call-external"),
        ]
        result = classify_call_sites(sites, [caller], macro_facts=[
            {
                "fact_id": "macro-object", "source_id": "sample.c",
                "macro_kind": "object", "terminal_name": "CALLBACK",
                "presence_status": "active",
            },
            {
                "fact_id": "macro-inactive", "source_id": "sample.c",
                "macro_kind": "function", "terminal_name": "TRACE",
                "presence_status": "inactive",
            },
        ])

        self.assertEqual(
            [
                ("CALLBACK", "external"),
                ("TRACE", "external"),
                ("customer_hook", "external"),
            ],
            [(edge.callee_id, edge.scope) for edge in result.edges],
        )
        self.assertEqual(0, result.dropped_macro)


def _caller() -> CodeNode:
    return CodeNode(
        id="code:sample.c:run", name="run", owner_id="code:sample.c",
        signature="void run(void)", file_path="sample.c", start_line=2,
        end_line=2, label="FUNCTION",
    )


def _call(caller: CodeNode, name: str, *, call_id: str = "trace-call") -> dict:
    return {
        "call_id": call_id,
        "caller_id": caller.id,
        "call_text": name,
        "callee_kind": "named",
        "presence_status": "active",
        "source_id": "sample.c",
        "file": "sample.c",
        "line": 2,
    }


if __name__ == "__main__":
    unittest.main()
