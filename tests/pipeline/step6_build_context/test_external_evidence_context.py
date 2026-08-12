"""Step6 external evidence selection contracts (spec 078).

Outgoing REFER_TO only; whole-chunk-or-skip; hash re-verification fail-closed;
no leak of other routines' evidence into prompt values.
"""
from __future__ import annotations

from dataclasses import replace
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from graph.schema.graph_edges import ReferTo
from graph.schema.graph_nodes import CodeNode, ExternalChunk
from graph.schema.node_ids import external_chunk_id, project_file_id
from pipeline.steps.step3_load_external.span_verifier import sha256_text
from pipeline.steps.step6_build_context.build_context_step import build_context_step
from pipeline.steps.step6_build_context.external_evidence import (
    ExternalEvidenceContractError,
    build_external_evidence_context,
)
from pipeline.steps.step6_build_context.framework_context_builder import FrameworkContextBuilder
from pipeline.steps.step7_analyze_code.batch_planner import Batch
from pipeline.steps.step7_analyze_code.framework_per_node import framework_analysis_targets
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

def _chunk(key: str, text: str, path: str = "app.cfgx") -> ExternalChunk:
    file_id = project_file_id("shop", path)
    return ExternalChunk(
        id=external_chunk_id(file_id, 0, len(text)),
        name=key,
        keys=(key,),
        semantic_kind="record",
        text=text,
        file_path=path,
        char_start=0,
        char_end=len(text),
        start_line=2,
        end_line=3,
        provenance="source_span",
        source_hash=sha256_text(text),
        content_hash=sha256_text(text),
    )


class ExternalEvidenceContextTest(unittest.IsolatedAsyncioTestCase):
    def test_only_outgoing_refer_to_is_selected_and_deduplicated(self) -> None:
        code = _code()
        linked = _chunk("ORDER.API", "A" * 1300)
        unrelated = _chunk("UNRELATED", "must-not-leak", "other.cfgx")
        state = {
            "external_chunks": [linked, unrelated],
            "external_edges": [
                ReferTo(code.id, linked.id, matched_key="ORDER.API"),
                ReferTo(code.id, linked.id, matched_key="ORDER.API"),
            ],
        }
        result = build_external_evidence_context(state, [code], char_budget=3000)
        self.assertEqual(1, len(result[code.id]))
        evidence = result[code.id][0]
        self.assertEqual("A" * 1300, evidence.text)
        self.assertEqual("ORDER.API", evidence.matched_key)
        self.assertNotIn("must-not-leak", json.dumps(evidence.as_prompt_dict()))

    def test_reverse_direction_edges_are_never_selected(self) -> None:
        code = _code()
        chunk = _chunk("orderService", "Z" * 100, "beans.xml")
        state = {
            "external_chunks": [chunk],
            "external_edges": [ReferTo(chunk.id, code.id, matched_key="orderService")],
        }
        self.assertEqual({}, build_external_evidence_context(state, [code]))

    def test_oversize_chunk_is_skipped_whole_never_truncated(self) -> None:
        code = _code()
        big = _chunk("BIG.KEY", "Z" * 1000, "big.cfgx")
        small = _chunk("SMALL.KEY", "s" * 50, "small.cfgx")
        state = {
            "external_chunks": [big, small],
            "external_edges": [
                ReferTo(code.id, big.id, matched_key="BIG.KEY"),
                ReferTo(code.id, small.id, matched_key="SMALL.KEY"),
            ],
        }
        result = build_external_evidence_context(state, [code], char_budget=300)
        texts = [item.text for item in result[code.id]]
        # 큰 청크는 통째로 생략, 작은 청크는 전문 포함 — 부분 절단은 존재하지 않는다.
        self.assertEqual(["s" * 50], texts)

    def test_damaged_hash_fails_before_context_injection(self) -> None:
        code = _code()
        chunk = replace(_chunk("ORDER.API", "payload"), content_hash="0" * 64)
        state = {
            "external_chunks": [chunk],
            "external_edges": [ReferTo(code.id, chunk.id, matched_key="ORDER.API")],
        }
        with self.assertRaises(ExternalEvidenceContractError):
            build_external_evidence_context(state, [code])

    async def test_build_context_and_both_prompt_owners_receive_same_exact_json(self) -> None:
        code = _code()
        linked = _chunk("ORDER.API", "/orders")
        unrelated = _chunk("UNRELATED", "must-not-leak", "other.cfgx")
        state = {
            "code_nodes": [code],
            "external_chunks": [linked, unrelated],
            "external_edges": [ReferTo(code.id, linked.id, matched_key="ORDER.API")],
            "reads_writes_edges": [],
            "calls_edges": [],
        }
        strategy = SimpleNamespace(
            context_builder=FrameworkContextBuilder(),
            analysis_targets=framework_analysis_targets,
        )
        with patch(
            "pipeline.steps.step6_build_context.build_context_step.build_control_subtrees",
            return_value={},
        ):
            result = await build_context_step(
                state,
                {"configurable": {"strategy": strategy, "source_cache": SourceFileCache()}},
            )
        context = result["analysis_contexts"][code.id]
        self.assertEqual("ORDER.API", context.external_evidence[0].matched_key)

        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "Order.java"
            source.write_text(code.code_text, encoding="utf-8")
            prompt_code = replace(code, file_path=str(source))
            context.file_path = str(source)
            framework_values, _inventory = _build_user_values(
                context, prompt_code, {code.id: context}, {code.id: prompt_code},
                "", {}, {}, SourceFileCache(),
            )
            batch_values, _ = _build_batch_values(
                Batch(None, (code.id,)),
                {code.id: context},
                {code.id: prompt_code},
                "",
                {},
                {},
                SourceFileCache(),
                body_fn=lambda *_: code.code_text,
            )

        self.assertIn('"matched_key":"ORDER.API"', framework_values["external_evidence"])
        self.assertIn('"numbered_text":"2 | /orders"', framework_values["external_evidence"])
        self.assertNotIn("must-not-leak", framework_values["external_evidence"])
        self.assertIn('"matched_key":"ORDER.API"', batch_values["nodes"])
        self.assertNotIn("must-not-leak", batch_values["nodes"])
        self.assertNotIn("domain_term_evidence", framework_values)
        self.assertNotIn("[DOMAIN_TERM_EVIDENCE]", batch_values["nodes"])


if __name__ == "__main__":
    unittest.main()
