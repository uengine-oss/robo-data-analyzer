from __future__ import annotations

import json
import unittest
from pathlib import Path
from unittest.mock import AsyncMock

from pipeline.models.human_glossary import HumanGlossary, HumanGlossaryTerm
from pipeline.steps.step8_post_processing.build_glossary_step import (
    _UPSERT_QUERY,
    build_glossary_step,
)


class HumanGlossaryStepTest(unittest.IsolatedAsyncioTestCase):
    async def test_absent_input_makes_no_graph_write_or_llm_call(self) -> None:
        neo = AsyncMock()
        glossary = HumanGlossary(Path("analysis/glossary.yaml"), False, None, None, ())
        result = await build_glossary_step({}, {
            "configurable": {"neo_client": neo, "human_glossary": glossary}
        })
        self.assertEqual({}, result)
        neo.run_batch_unwind.assert_not_awaited()

    async def test_supplied_terms_are_stored_verbatim_with_provenance(self) -> None:
        neo = AsyncMock()
        glossary = HumanGlossary(
            Path("analysis/glossary.yaml"), True, "1.0", "a" * 64,
            (HumanGlossaryTerm("승인 대기", ("결제 대기",), "승인을 기다림", "상태"),),
        )
        await build_glossary_step({}, {
            "configurable": {"neo_client": neo, "human_glossary": glossary}
        })
        query, items = neo.run_batch_unwind.await_args.args
        self.assertEqual(_UPSERT_QUERY, query)
        self.assertEqual("human", items[0]["source_kind"])
        self.assertEqual("a" * 64, items[0]["source_sha256"])
        payload = json.loads(items[0]["terms_json"])
        self.assertEqual([{
            "canonical": "승인 대기",
            "colloquial": ["결제 대기"],
            "meaning": "승인을 기다림",
            "category": "상태",
        }], payload["terms"])
        self.assertEqual([], payload["actions"])

    async def test_missing_validated_dependencies_fail_closed(self) -> None:
        with self.assertRaisesRegex(ValueError, "neo_client"):
            await build_glossary_step({}, {"configurable": {}})
        with self.assertRaisesRegex(ValueError, "human_glossary"):
            await build_glossary_step({}, {"configurable": {"neo_client": AsyncMock()}})


if __name__ == "__main__":
    unittest.main()
