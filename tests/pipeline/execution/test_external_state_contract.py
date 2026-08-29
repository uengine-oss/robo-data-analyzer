"""WorkflowState ownership and reducers for external-file linking."""
from __future__ import annotations

import unittest
from typing import Annotated, get_origin, get_type_hints

from pipeline.execution.workflow_state import WorkflowState


class ExternalStateContractTest(unittest.TestCase):
    def test_external_state_keys_and_only_multi_producer_reducers(self) -> None:
        hints = get_type_hints(WorkflowState, include_extras=True)
        expected = {
            "external_source_files",
            "external_source_records",
            "resolved_external_reference_links",
            "external_reference_ledger",
        }
        self.assertTrue(expected.issubset(hints))
        # spec 078: 폐기된 외부 state 키는 존재해서는 안 된다.
        for removed in ("document_nodes", "unresolved_refs", "candidate_links"):
            self.assertNotIn(removed, hints)

        self.assertFalse({
            name for name in expected if get_origin(hints[name]) is Annotated
        })


if __name__ == "__main__":
    unittest.main()
