from __future__ import annotations

import unittest
from pathlib import Path

from pipeline.models.human_glossary import HumanGlossary, HumanGlossaryTerm
from pipeline.publication.glossary import (
    GlossaryProjectionError,
    build_glossary_write_batch,
)


SCOPE = "analysis-scope/v1:" + "a" * 64
GENERATION = "run:20260828"


class GlossaryPublicationTest(unittest.TestCase):
    def test_supplied_terms_publish_exact_human_values_without_file_metadata(self) -> None:
        glossary = HumanGlossary(
            Path("analysis/glossary.yaml"),
            True,
            "1.0",
            "a" * 64,
            (HumanGlossaryTerm(
                "승인 대기", ("결제 대기",), "승인을 기다림", "상태",
            ),),
        )
        batch = build_glossary_write_batch(
            analysis_scope_id=SCOPE,
            generation_id=GENERATION,
            glossary=glossary,
        )

        self.assertEqual(1, len(batch.node_create_operations))
        properties = dict(batch.node_create_operations[0].present_properties)
        self.assertEqual("승인 대기", properties["canonical_term"])
        self.assertEqual(("결제 대기",), properties["aliases"])
        self.assertNotIn("source_path", properties)
        self.assertNotIn("source_sha256", properties)
        self.assertNotIn("schema_version", properties)

    def test_absent_glossary_is_an_explicit_empty_stage(self) -> None:
        batch = build_glossary_write_batch(
            analysis_scope_id=SCOPE,
            generation_id=GENERATION,
            glossary=HumanGlossary(
                Path("analysis/glossary.yaml"), False, None, None, (),
            ),
        )
        self.assertEqual((), batch.expectations)
        self.assertEqual("glossary", batch.stage_name)

    def test_invalid_provenance_and_cross_term_spelling_collision_fail(self) -> None:
        with self.assertRaisesRegex(GlossaryProjectionError, "schema/hash"):
            build_glossary_write_batch(
                analysis_scope_id=SCOPE,
                generation_id=GENERATION,
                glossary=HumanGlossary(
                    Path("analysis/glossary.yaml"), True, None, None, (),
                ),
            )

        collision = HumanGlossary(
            Path("analysis/glossary.yaml"),
            True,
            "1.0",
            "b" * 64,
            (
                HumanGlossaryTerm("승인 대기", ("결제 대기",), "A", "상태"),
                HumanGlossaryTerm("결제 대기", (), "B", "상태"),
            ),
        )
        with self.assertRaisesRegex(GlossaryProjectionError, "globally unique"):
            build_glossary_write_batch(
                analysis_scope_id=SCOPE,
                generation_id=GENERATION,
                glossary=collision,
            )


if __name__ == "__main__":
    unittest.main()
