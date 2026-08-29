from __future__ import annotations

import hashlib
import unittest

from pipeline.publication.search_segments import (
    SearchSegmentsProjectionError,
    build_search_segments_write_batch,
    search_segment_stable_id,
)


SCOPE = "analysis-scope/v1:" + "a" * 64
GENERATION = "run:20260828"


def _segment(*, ordinal: int = 1, text: str = "주문 상태를 검증한다.") -> dict:
    text_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return {
        "stable_id": search_segment_stable_id(
            "CodeNode", "code:orders.validate", ordinal, text_hash,
        ),
        "parent_product_type": "CodeNode",
        "parent_stable_id": "code:orders.validate",
        "ordinal": ordinal,
        "source_start_line": 10,
        "source_end_line": 30,
        "text_sha256": text_hash,
        "text": text,
        "embedding_model_id": "embed-v1",
        "embedding": [0.25, -0.5],
    }


class SearchSegmentsPublicationTest(unittest.TestCase):
    def test_segment_preserves_full_text_vector_and_parent_relation(self) -> None:
        batch = build_search_segments_write_batch(
            analysis_scope_id=SCOPE,
            generation_id=GENERATION,
            segments=[_segment()],
        )
        self.assertEqual(1, len(batch.node_create_operations))
        self.assertEqual(1, len(batch.relationship_create_operations))
        properties = dict(batch.node_create_operations[0].present_properties)
        self.assertEqual("주문 상태를 검증한다.", properties["text"])
        self.assertEqual((0.25, -0.5), properties["embedding"])
        self.assertNotIn("parent_stable_id", properties)
        self.assertNotIn("ordinal", properties)
        self.assertEqual(
            "HAS_SEARCH_SEGMENT",
            batch.relationship_create_operations[0].identity.relationship_type,
        )

    def test_hash_identity_vector_and_ordinal_contract_fail_closed(self) -> None:
        bad_hash = _segment()
        bad_hash["text_sha256"] = "0" * 64
        bad_vector = _segment()
        bad_vector["embedding"] = [1]
        noncontiguous = _segment(ordinal=2)
        cases = (
            (bad_hash, "text hash mismatch"),
            (bad_vector, "finite floats"),
            (noncontiguous, "not contiguous"),
        )
        for item, message in cases:
            with self.subTest(message=message), self.assertRaisesRegex(
                SearchSegmentsProjectionError, message,
            ):
                build_search_segments_write_batch(
                    analysis_scope_id=SCOPE,
                    generation_id=GENERATION,
                    segments=[item],
                )

    def test_source_lines_are_both_present_or_both_absent(self) -> None:
        segment = _segment()
        del segment["source_end_line"]
        with self.assertRaisesRegex(SearchSegmentsProjectionError, "both present"):
            build_search_segments_write_batch(
                analysis_scope_id=SCOPE,
                generation_id=GENERATION,
                segments=[segment],
            )


if __name__ == "__main__":
    unittest.main()
