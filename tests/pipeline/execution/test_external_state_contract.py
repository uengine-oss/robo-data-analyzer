"""WorkflowState ownership and reducers for external-file linking."""
from __future__ import annotations

import operator
import unittest
from typing import Annotated, get_args, get_origin, get_type_hints

from pipeline.execution.workflow_state import WorkflowState


class ExternalStateContractTest(unittest.TestCase):
    def test_external_state_keys_and_only_multi_producer_reducers(self) -> None:
        hints = get_type_hints(WorkflowState, include_extras=True)
        expected = {
            "project_files", "external_chunks", "document_nodes", "external_edges",
            "unresolved_refs", "candidate_links",
        }
        self.assertTrue(expected.issubset(hints))

        reduced = {name for name in expected if get_origin(hints[name]) is Annotated}
        self.assertEqual(reduced, {"external_edges", "unresolved_refs"})
        for name in reduced:
            self.assertIs(get_args(hints[name])[-1], operator.add)


if __name__ == "__main__":
    unittest.main()
