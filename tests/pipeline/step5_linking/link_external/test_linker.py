"""호출부 기반 REFER_TO 링커 vs. gold (spec 081).

``gold/linking_cases.json`` 의 모든 케이스를 채점한다. 기대한 연결이 정확히 나와야 하고
그 밖의 연결이 하나라도 있으면 실패다 — 어떤 케이스든 오연결은 스위트 실패다.
"""
from __future__ import annotations

import json
from pathlib import Path
import unittest

from graph.schema.graph_edges import References
from graph.schema.graph_nodes import CodeNode, Constant, ExternalChunk
from graph.schema.node_ids import external_chunk_id, project_file_id
from pipeline.steps.step3_load_external.span_verifier import sha256_text
from pipeline.steps.step5_linking.link_external.linker import link_external


GOLD_PATH = Path(__file__).resolve().parent / "gold" / "linking_cases.json"


def _chunks_for(keys_spec: dict[str, int], addressable: bool = True) -> list[ExternalChunk]:
    """키마다 ``count`` 개의 조각을 각기 다른 파일에 만든다."""
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
                addressable_keys=(key,) if addressable else (),
            ))
    return chunks


class LinkerGoldTest(unittest.TestCase):
    def test_every_gold_case_matches_exactly(self) -> None:
        cases = json.loads(GOLD_PATH.read_text(encoding="utf-8"))["cases"]
        self.assertGreaterEqual(len(cases), 8)
        for case in cases:
            with self.subTest(case=case["name"]):
                chunks = _chunks_for(case["chunks"], case.get("addressable", True))
                code = CodeNode(
                    id="code:shop:Svc:a", signature="a()", file_path="Svc.java",
                    start_line=10, end_line=20, label="METHOD", name="a",
                    code_text=case["code"], owner_id="code:shop:Svc",
                )
                constants = []
                references = []
                if "constant" in case:
                    constant = Constant(
                        id="code:shop:Svc:KEY", owner_id=code.owner_id,
                        name=case["constant"]["name"], file_path=code.file_path,
                        start_line=3, end_line=3, label="CONSTANT_FIELD",
                        value=f'"{case["constant"]["value"]}"',
                    )
                    constants.append(constant)
                    # 이름 해석은 step2 REFERENCES 가 한다 — 테스트도 그 사실을 준다.
                    references.append(References(code.id, constant.id, 4))
                outcome = link_external(
                    code_nodes=[code],
                    constants=constants,
                    chunks=chunks,
                    routine_ids={code.id},
                    references_edges=references,
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
            addressable_keys=("ORDER.API",),
        )
        code = CodeNode(
            id="code:shop:Svc:a", signature="a()", file_path="Svc.java",
            start_line=1, end_line=2, label="METHOD", name="a",
            code_text='sql.selectOne("ORDER.API", id);', owner_id="code:shop:Svc",
        )
        outcome = link_external(
            code_nodes=[code], constants=[],
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
            addressable_keys=("OrderMapper.getOrderDetail", "getOrderDetail"),
        )
        code = CodeNode(
            id="code:shop:Svc:a", signature="a()", file_path="Svc.java",
            start_line=1, end_line=3, label="METHOD", name="a",
            code_text='sql.selectOne("getOrderDetail", p); '
                      'sql.selectList("OrderMapper.getOrderDetail", p);',
            owner_id="code:shop:Svc",
        )
        outcome = link_external(
            code_nodes=[code], constants=[],
            chunks=[chunk], routine_ids={code.id},
        )
        self.assertEqual(1, len(outcome.external_edges))
        self.assertEqual(chunk.id, outcome.external_edges[0].target_id)

    def test_dynamic_key_sites_are_counted_not_linked(self) -> None:
        code = CodeNode(
            id="code:shop:Svc:a", signature="a()", file_path="Svc.java",
            start_line=1, end_line=3, label="METHOD", name="a",
            code_text='sql.selectOne(buildKey(), p);', owner_id="code:shop:Svc",
        )
        outcome = link_external(
            code_nodes=[code], constants=[],
            chunks=_chunks_for({"ORDER.API": 1}), routine_ids={code.id},
        )
        self.assertEqual([], outcome.external_edges)
        self.assertEqual(1, outcome.dynamic_key_sites)


if __name__ == "__main__":
    unittest.main()
