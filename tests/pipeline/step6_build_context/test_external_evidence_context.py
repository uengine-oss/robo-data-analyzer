from __future__ import annotations

from dataclasses import replace
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from graph.schema.graph_edges import Mentions, ReferTo, UsesConfig
from graph.schema.graph_nodes import CandidateLink, CodeNode, DocumentNode
from graph.schema.node_ids import project_file_id
from pipeline.models.analysis_context import AnalysisContext
from pipeline.steps.step3_load_external.chunking import make_external_chunks
from pipeline.steps.step6_build_context.build_context_step import build_context_step
from pipeline.steps.step6_build_context.external_evidence import (
    ExternalEvidenceContractError,
    build_external_evidence_context,
)
from pipeline.steps.step6_build_context.framework_context_builder import FrameworkContextBuilder
from pipeline.steps.step7_analyze_code.batch_planner import Batch
from pipeline.steps.step7_analyze_code.batch_prompt_values import _build_batch_values
from pipeline.steps.step7_analyze_code.three_split_llm_fanout import _build_user_values
from shared.files.cache import SourceFileCache


def _code(code_id: str = "code:shop.Order:run") -> CodeNode:
    return CodeNode(
        id=code_id,
        signature="run()",
        file_path="Order.java",
        start_line=1,
        end_line=1,
        label="METHOD",
        name="run",
        code_text="void run() {}",
        owner_id="code:shop.Order",
    )


def _chunks(key: str, text: str, path: str = "app.cfgx"):
    return make_external_chunks(
        file_id=project_file_id("shop", path),
        key=key,
        kind="custom",
        text=text,
        file_path=path,
        start_line=2,
        end_line=3,
    )[0]


class ExternalEvidenceContextTest(unittest.IsolatedAsyncioTestCase):
    def test_exact_document_mention_is_injected_without_candidate_promotion(self) -> None:
        code = _code()
        text = "run 호출은 주문을 저장한다."
        document = DocumentNode(
            "file:guide:chunk", "DOC_CHUNK", "chunk 1", "", text,
            "guide.md", 5, 5, 1, "file:guide:root",
            hashlib.sha256(text.encode()).hexdigest(),
        )
        state = {
            "external_chunks": [],
            "document_nodes": [document],
            "external_edges": [Mentions(document.id, code.id, "run", "guide.md", 5)],
        }
        evidence = build_external_evidence_context(state, [code])[code.id][0]
        self.assertEqual(("document", text), (evidence.kind, evidence.text))

    def test_damaged_document_hash_fails_before_context_injection(self) -> None:
        code = _code()
        document = DocumentNode(
            "file:guide:chunk", "DOC_CHUNK", "chunk 1", "", "changed",
            "guide.md", 5, 5, 1, "file:guide:root", "0" * 64,
        )
        with self.assertRaises(ExternalEvidenceContractError):
            build_external_evidence_context({
                "external_chunks": [],
                "document_nodes": [document],
                "external_edges": [Mentions(document.id, code.id)],
            }, [code])

    def test_only_exact_links_are_reassembled_sorted_and_deduplicated(self) -> None:
        code = _code()
        linked = _chunks("ORDER.API", "A" * 1300)
        unrelated = _chunks("UNRELATED", "must-not-leak", "other.cfgx")
        state = {
            "external_chunks": [*linked, *unrelated],
            "external_edges": [
                UsesConfig(code.id, linked[0].id),
                UsesConfig(code.id, linked[0].id),
            ],
            "candidate_links": [CandidateLink(
                id="file:candidate", name="candidate", source_id=code.id,
                target_id=unrelated[0].id, score=0.99, model="test",
            )],
        }

        result = build_external_evidence_context(state, [code], char_budget=3000)

        self.assertEqual(1, len(result[code.id]))
        evidence = result[code.id][0]
        self.assertEqual("A" * 1300, evidence.text)
        self.assertFalse(evidence.truncated)
        self.assertNotIn("must-not-leak", json.dumps(evidence.as_prompt_dict()))

    def test_module_fqn_link_is_relevant_to_members_and_budget_is_hard(self) -> None:
        code = _code()
        artifact = _chunks("orderService", "Z" * 1000, "beans.xml")
        state = {
            "external_chunks": artifact,
            "external_edges": [ReferTo(artifact[0].id, code.owner_id, matched_fqn="shop.Order")],
        }

        evidence = build_external_evidence_context(state, [code], char_budget=240)[code.id][0]

        self.assertTrue(evidence.truncated)
        self.assertGreater(len(evidence.text), 0)
        self.assertLessEqual(
            len(evidence.text) + len(evidence.file_path) + len(evidence.key) + len(evidence.kind) + 96,
            240,
        )

    def test_damaged_part_sequence_fails_before_context_injection(self) -> None:
        code = _code()
        chunks = _chunks("ORDER.API", "A" * 1300)
        chunks[1] = replace(chunks[1], part_index=2)
        state = {
            "external_chunks": chunks,
            "external_edges": [UsesConfig(code.id, chunks[0].id)],
        }

        with self.assertRaises(ExternalEvidenceContractError):
            build_external_evidence_context(state, [code])

    async def test_build_context_and_both_prompt_owners_receive_same_exact_json(self) -> None:
        code = _code()
        linked = _chunks("ORDER.API", "/orders")
        unrelated = _chunks("UNRELATED", "must-not-leak", "other.cfgx")
        state = {
            "code_nodes": [code],
            "external_chunks": [*linked, *unrelated],
            "external_edges": [UsesConfig(code.id, linked[0].id)],
            "reads_writes_edges": [],
            "calls_edges": [],
        }
        strategy = SimpleNamespace(context_builder=FrameworkContextBuilder())
        with patch(
            "pipeline.steps.step6_build_context.build_context_step.build_control_subtrees",
            return_value={},
        ):
            result = await build_context_step(
                state,
                {"configurable": {"strategy": strategy, "source_cache": SourceFileCache()}},
            )
        context = result["analysis_contexts"][code.id]
        self.assertEqual("ORDER.API", context.external_evidence[0].key)

        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "Order.java"
            source.write_text(code.code_text, encoding="utf-8")
            prompt_code = replace(code, file_path=str(source))
            context.file_path = str(source)
            framework_values = _build_user_values(
                context, prompt_code, {code.id: context}, "", {}, {}, SourceFileCache(),
            )
        batch_values, _ = _build_batch_values(
            Batch(None, (code.id,)),
            {code.id: context},
            {code.id: code},
            "",
            {},
            {},
            SourceFileCache(),
            body_fn=lambda *_: code.code_text,
        )

        self.assertIn('"key":"ORDER.API"', framework_values["external_evidence"])
        self.assertNotIn("must-not-leak", framework_values["external_evidence"])
        self.assertIn('"key":"ORDER.API"', batch_values["nodes"])
        self.assertNotIn("must-not-leak", batch_values["nodes"])


if __name__ == "__main__":
    unittest.main()
