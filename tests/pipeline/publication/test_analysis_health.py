from __future__ import annotations

import unittest

from pipeline.publication.analysis_health import (
    AnalysisHealthProjectionError,
    build_analysis_health_write_batch,
)


SCOPE = "analysis-scope/v1:" + "a" * 64
GENERATION = "run:20260828"


class AnalysisHealthPublicationTest(unittest.TestCase):
    def test_clean_health_ledger_publishes_one_immutable_node(self) -> None:
        batch = build_analysis_health_write_batch(
            analysis_scope_id=SCOPE,
            generation_id=GENERATION,
            missing_total=0,
            duplicate_total=0,
            unresolved_call_count=3,
            unresolved_data_object_count=2,
            prepublish_wall_time_ms=1234,
            prepublish_performance_artifact_hash="a" * 64,
        )
        self.assertEqual(1, len(batch.node_create_operations))
        self.assertEqual((), batch.relationship_create_operations)
        properties = dict(batch.node_create_operations[0].present_properties)
        self.assertEqual(f"{GENERATION}::health", properties["stable_id"])
        self.assertEqual(3, properties["unresolved_call_count"])

    def test_missing_duplicate_negative_and_bad_hash_fail_closed(self) -> None:
        base = {
            "analysis_scope_id": SCOPE,
            "generation_id": GENERATION,
            "missing_total": 0,
            "duplicate_total": 0,
            "unresolved_call_count": 0,
            "unresolved_data_object_count": 0,
            "prepublish_wall_time_ms": 1,
            "prepublish_performance_artifact_hash": "a" * 64,
        }
        cases = (
            ({"missing_total": 1}, "cannot publish"),
            ({"duplicate_total": 1}, "cannot publish"),
            ({"unresolved_call_count": -1}, "non-negative"),
            ({"prepublish_performance_artifact_hash": "BAD"}, "lowercase SHA-256"),
        )
        for update, message in cases:
            with self.subTest(message=message), self.assertRaisesRegex(
                AnalysisHealthProjectionError, message,
            ):
                build_analysis_health_write_batch(**(base | update))


if __name__ == "__main__":
    unittest.main()
