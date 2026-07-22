"""Precision-first REFER_TO linker vs. gold cases (spec 078, T103/T309).

Every case in ``gold/linking_cases.json`` is scored: expected links must be
produced exactly (matched_key + via_symbol) and nothing else — false positives
in any case fail the suite.
"""
from __future__ import annotations

import json
from pathlib import Path
import unittest

from graph.schema.graph_nodes import CodeNode, Constant, ExternalChunk
from graph.schema.node_ids import external_chunk_id, project_file_id
from pipeline.steps.step3_load_external.span_verifier import sha256_text
from pipeline.steps.step5_linking.link_external.linker import link_external


GOLD_PATH = Path(__file__).resolve().parent / "gold" / "linking_cases.json"


def _chunks_for(keys_spec: dict[str, int]) -> list[ExternalChunk]:
    """Build ``count`` chunks per key, each in its own file."""
    chunks: list[ExternalChunk] = []
    for key, count in keys_spec.items():
        for index in range(count):
            relpath = f"external/{key.replace('.', '_')}_{index}.yaml"
            file_id = project_file_id("shop", relpath)
            text = f"{key}: value-{index}\n"
            chunks.append(ExternalChunk(
                id=external_chunk_id(file_id, 0, len(text)),
                name=key,
                keys=(key,),
                semantic_kind="yaml_entry",
                text=text,
                file_path=relpath,
                char_start=0,
                char_end=len(text),
                start_line=1,
                end_line=1,
                provenance="source_span",
                source_hash=sha256_text(text),
                content_hash=sha256_text(text),
            ))
    return chunks


class LinkerGoldTest(unittest.TestCase):
    def test_every_gold_case_matches_exactly(self) -> None:
        cases = json.loads(GOLD_PATH.read_text(encoding="utf-8"))["cases"]
        self.assertGreaterEqual(len(cases), 8)
        for case in cases:
            with self.subTest(case=case["name"]):
                chunks = _chunks_for(case["chunks"])
                code = CodeNode(
                    id="code:shop:Svc:a", signature="a()", file_path="Svc.java",
                    start_line=10, end_line=20, label="METHOD", name="a",
                    code_text=case["code"], owner_id="code:shop:Svc",
                )
                constants = []
                if "constant" in case:
                    constants.append(Constant(
                        id="code:shop:Svc:KEY", owner_id=code.owner_id,
                        name=case["constant"]["name"], file_path=code.file_path,
                        start_line=3, end_line=3, label="CONSTANT_FIELD",
                        value=f'"{case["constant"]["value"]}"',
                    ))
                outcome = link_external(
                    code_nodes=[code],
                    variables=[],
                    constants=constants,
                    chunks=chunks,
                    routine_ids={code.id},
                )
                actual = sorted(
                    (edge.matched_key, edge.via_symbol) for edge in outcome.external_edges
                )
                expected = sorted(
                    (item["matched_key"], item["via_symbol"]) for item in case["expected"]
                )
                self.assertEqual(expected, actual)
                for edge in outcome.external_edges:
                    self.assertEqual("code:shop:Svc:a", edge.source_id)

    def test_derived_chunks_are_never_link_targets(self) -> None:
        text = "converted content with ORDER.API inside"
        derived = ExternalChunk(
            id="file:shop/doc.pdf:chunk:derived-0", name="doc", keys=(),
            semantic_kind="derived_document", text=text, file_path="doc.pdf",
            char_start=0, char_end=0, start_line=0, end_line=0,
            provenance="derived_document", source_hash="", content_hash=sha256_text(text),
        )
        code = CodeNode(
            id="code:shop:Svc:a", signature="a()", file_path="Svc.java",
            start_line=1, end_line=2, label="METHOD", name="a",
            code_text='client.get("ORDER.API");', owner_id="code:shop:Svc",
        )
        outcome = link_external(
            code_nodes=[code], variables=[], constants=[],
            chunks=[derived], routine_ids={code.id},
        )
        self.assertEqual([], outcome.external_edges)

    def test_same_chunk_via_two_keys_yields_single_edge(self) -> None:
        relpath = "mapper/OrderMapper.xml"
        file_id = project_file_id("shop", relpath)
        text = '<select id="getOrderDetail">SELECT 1</select>'
        chunk = ExternalChunk(
            id=external_chunk_id(file_id, 0, len(text)),
            name="getOrderDetail",
            keys=("OrderMapper.getOrderDetail", "getOrderDetail"),
            semantic_kind="xml_element",
            text=text, file_path=relpath,
            char_start=0, char_end=len(text), start_line=1, end_line=1,
            provenance="source_span",
            source_hash=sha256_text(text), content_hash=sha256_text(text),
        )
        code = CodeNode(
            id="code:shop:Svc:a", signature="a()", file_path="Svc.java",
            start_line=1, end_line=3, label="METHOD", name="a",
            code_text='sql.selectOne("getOrderDetail"); sql.count("OrderMapper.getOrderDetail");',
            owner_id="code:shop:Svc",
        )
        outcome = link_external(
            code_nodes=[code], variables=[], constants=[],
            chunks=[chunk], routine_ids={code.id},
        )
        self.assertEqual(1, len(outcome.external_edges))
        self.assertEqual(chunk.id, outcome.external_edges[0].target_id)


if __name__ == "__main__":
    unittest.main()
