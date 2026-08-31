"""Step6 external evidence selection contracts (spec 078).

Outgoing REFER_TO only; every linked chunk is preserved whole; hash re-verification
fails closed; no leak of other routines' evidence into prompt values.
"""
from __future__ import annotations

from dataclasses import replace
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from graph.schema.graph_nodes import CodeNode
from graph.schema.node_ids import external_chunk_id, project_file_id
from pipeline.models.external_reference_resolution import ResolvedExternalReferenceLink
from pipeline.models.external_source import ExternalSourceRecord
from pipeline.steps.step3_load_external.span_verifier import sha256_text
from pipeline.steps.step6_build_context.build_context_step import build_context_step
from pipeline.steps.step6_build_context.external_evidence import (
    ExternalEvidenceContractError,
    build_external_evidence_context,
)
from pipeline.steps.step6_build_context.framework_context_builder import FrameworkContextBuilder
from pipeline.steps.step7_analyze_code.framework_per_node import framework_analysis_targets
from pipeline.steps.step7_analyze_code.three_split_llm_fanout import (
    _prepare_analysis_inputs,
    _summary_prompt_values,
)
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

def _chunk(key: str, text: str, path: str = "app.cfgx") -> ExternalSourceRecord:
    file_id = project_file_id("shop", path)
    record_id = external_chunk_id(file_id, 0, len(text))
    return ExternalSourceRecord(
        source_record_id=record_id,
        source_file_id=file_id,
        external_adapter_id="analyzer.external.test-record",
        external_adapter_version="test/v1",
        addressable_keys=(key,),
        semantic_kind="record",
        exact_text=text,
        project_relative_path=path,
        source_character_start=0,
        source_character_end=len(text),
        source_start_line=2,
        source_end_line=3,
        provenance="source_span",
        source_sha256=sha256_text(text),
        content_sha256=sha256_text(text),
    )


class ExternalEvidenceContextTest(unittest.IsolatedAsyncioTestCase):
    def test_only_outgoing_refer_to_is_selected_and_deduplicated(self) -> None:
        code = _code()
        linked = _chunk("ORDER.API", "A" * 1300)
        unrelated = _chunk("UNRELATED", "must-not-leak", "other.cfgx")
        state = {
            "external_source_records": [linked, unrelated],
            "resolved_external_reference_links": [
                ResolvedExternalReferenceLink(
                    code.id, linked.source_record_id, "ORDER.API",
                ),
                ResolvedExternalReferenceLink(
                    code.id, linked.source_record_id, "ORDER.API",
                ),
            ],
        }
        result = build_external_evidence_context(state, [code])
        self.assertEqual(1, len(result[code.id]))
        evidence = result[code.id][0]
        self.assertEqual("A" * 1300, evidence.text)
        self.assertEqual("ORDER.API", evidence.matched_key)
        self.assertNotIn("must-not-leak", evidence.text)

    def test_reverse_direction_edges_are_never_selected(self) -> None:
        code = _code()
        chunk = _chunk("orderService", "Z" * 100, "beans.xml")
        state = {
            "external_source_records": [chunk],
            "resolved_external_reference_links": [
                ResolvedExternalReferenceLink(
                    chunk.source_record_id, code.id, "orderService",
                )
            ],
        }
        self.assertEqual({}, build_external_evidence_context(state, [code]))

    def test_large_chunk_is_preserved_whole_for_downstream_obligation_splitting(self) -> None:
        code = _code()
        big = _chunk("BIG.KEY", "Z" * 1000, "big.cfgx")
        small = _chunk("SMALL.KEY", "s" * 50, "small.cfgx")
        state = {
            "external_source_records": [big, small],
            "resolved_external_reference_links": [
                ResolvedExternalReferenceLink(
                    code.id, big.source_record_id, "BIG.KEY",
                ),
                ResolvedExternalReferenceLink(
                    code.id, small.source_record_id, "SMALL.KEY",
                ),
            ],
        }
        result = build_external_evidence_context(state, [code])
        texts = [item.text for item in result[code.id]]
        self.assertEqual(["Z" * 1000, "s" * 50], texts)

    def test_more_than_eight_linked_chunks_are_all_preserved(self) -> None:
        code = _code()
        chunks = [
            _chunk(f"KEY.{index:02d}", f"payload-{index}", f"{index:02d}.cfgx")
            for index in range(12)
        ]
        state = {
            "external_source_records": chunks,
            "resolved_external_reference_links": [
                ResolvedExternalReferenceLink(
                    code.id,
                    chunk.source_record_id,
                    chunk.addressable_keys[0],
                )
                for chunk in chunks
            ],
        }

        result = build_external_evidence_context(state, [code])

        self.assertEqual(12, len(result[code.id]))
        self.assertEqual(
            [f"payload-{index}" for index in range(12)],
            [item.text for item in result[code.id]],
        )

    def test_damaged_hash_fails_before_context_injection(self) -> None:
        code = _code()
        chunk = replace(
            _chunk("ORDER.API", "payload"), content_sha256="0" * 64,
        )
        state = {
            "external_source_records": [chunk],
            "resolved_external_reference_links": [
                ResolvedExternalReferenceLink(
                    code.id, chunk.source_record_id, "ORDER.API",
                )
            ],
        }
        with self.assertRaises(ExternalEvidenceContractError):
            build_external_evidence_context(state, [code])

    async def test_build_context_and_prompt_receive_only_assigned_external_text(self) -> None:
        code = _code()
        linked = _chunk("ORDER.API", "/orders")
        unrelated = _chunk("UNRELATED", "must-not-leak", "other.cfgx")
        state = {
            "code_nodes": [code],
            "external_source_records": [linked, unrelated],
            "resolved_external_reference_links": [
                ResolvedExternalReferenceLink(
                    code.id, linked.source_record_id, "ORDER.API",
                )
            ],
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
            framework_values, _inventory = _prepare_analysis_inputs(
                context, prompt_code, {code.id: context}, {code.id: prompt_code},
                SourceFileCache(),
            )
            profile, _all_specs, concerns = context.extra["_semantic_frame_plan"]
            summary_values = _summary_prompt_values(
                profile,
                concerns["summary"],
                framework_values,
            )

        linked_sources = framework_values["external_source_text_by_id"]
        self.assertEqual({linked.source_record_id: "/orders"}, linked_sources)
        self.assertNotIn("matched_key", summary_values["structural_profile"])
        self.assertIn('"matched_key":"ORDER.API"', summary_values["linked_external_sources"])
        self.assertIn("/orders", summary_values["linked_external_sources"])
        self.assertNotIn("must-not-leak", summary_values["linked_external_sources"])
        self.assertNotIn("domain_term_evidence", framework_values)
        self.assertNotIn(
            "[DOMAIN_TERM_EVIDENCE]", summary_values["linked_external_sources"],
        )


if __name__ == "__main__":
    unittest.main()
