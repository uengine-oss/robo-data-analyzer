"""참조 대상 사전의 조회 계약 (spec 081)."""
from __future__ import annotations

import unittest

from graph.schema.graph_nodes import ExternalChunk, ProjectFile
from pipeline.steps.step3_load_external.span_verifier import sha256_text
from pipeline.steps.step5_linking.link_external.target_index import (
    TargetIndex,
    suffix_candidates,
)


def _chunk(chunk_id: str, keys: tuple[str, ...], relpath: str,
           addressable: bool = True) -> ExternalChunk:
    text = f"<e>{keys[0]}</e>"
    return ExternalChunk(
        id=chunk_id, name=keys[0], keys=keys, semantic_kind="xml_element", text=text,
        file_path=relpath, char_start=0, char_end=len(text), start_line=1, end_line=1,
        provenance="source_span", source_hash=sha256_text(text), content_hash=sha256_text(text),
        addressable_keys=keys if addressable else (),
    )


def _file(file_id: str, relpath: str) -> ProjectFile:
    return ProjectFile(id=file_id, name=relpath.rsplit("/", 1)[-1], project="p",
                       relpath=relpath, ext="." + relpath.rsplit(".", 1)[-1],
                       category="config", status="registered")


class SuffixCandidateTest(unittest.TestCase):
    def test_delimiter_boundary_suffixes_only(self) -> None:
        self.assertEqual(
            ["LoanApplicationSession"],
            suffix_candidates("java:comp/env/ejb/LoanApplicationSession"),
        )

    def test_whole_value_comes_first_when_key_shaped(self) -> None:
        self.assertEqual(["shop.order.find"], suffix_candidates("shop.order.find"))

    def test_values_containing_whitespace_are_never_split(self) -> None:
        # 실측 FP: " for order " 의 끝 단어가 엔티티 'order' 와 겹쳤다.
        self.assertEqual([], suffix_candidates("for order"))
        self.assertEqual([], suffix_candidates("failed to load config.xml"))

    def test_path_template_still_yields_the_file_name(self) -> None:
        self.assertEqual(["subscribers.dat"], suffix_candidates("%s/subscribers.dat"))


class TargetLookupTest(unittest.TestCase):
    def setUp(self) -> None:
        self.chunks = [
            _chunk("c1", ("OrderBean",), "conf/beans.xml"),
            _chunk("c2", ("Shared",), "conf/beans.xml"),
            _chunk("c3", ("Shared",), "conf/other.xml"),
            _chunk("c4", ("orderId",), "conf/model.xml", addressable=False),
        ]
        self.files = [_file("f1", "conf/beans.xml"), _file("f2", "conf/other.xml"),
                      _file("f3", "data/in/recv.dat"), _file("f4", "src/App.java")]
        self.index = TargetIndex(self.chunks, self.files)

    def test_addressable_chunk_key_resolves(self) -> None:
        hits = self.index.lookup("java:comp/env/ejb/OrderBean")
        self.assertEqual(["c1"], [hit.target_id for hit in hits])
        self.assertEqual("OrderBean", hits[0].matched_key)

    def test_key_present_but_not_addressable_is_never_a_target(self) -> None:
        self.assertEqual([], self.index.lookup("orderId"))

    def test_same_key_in_several_addressable_chunks_links_all(self) -> None:
        hits = self.index.lookup("Shared")
        self.assertEqual(["c2", "c3"], sorted(hit.target_id for hit in hits))

    def test_file_name_resolves_when_unique(self) -> None:
        hits = self.index.lookup("recv.dat")
        self.assertEqual(["f3"], [hit.target_id for hit in hits])

    def test_path_suffix_resolves(self) -> None:
        hits = self.index.lookup("./conf/other.xml")
        self.assertEqual(["f2"], [hit.target_id for hit in hits])

    def test_self_reference_is_not_linked(self) -> None:
        self.assertEqual([], self.index.lookup("App.java", source_file="src/App.java"))

    def test_extensionless_name_is_not_a_bare_name_target(self) -> None:
        index = TargetIndex([], [_file("f9", "docs/Makefile")])
        self.assertEqual([], index.lookup("Makefile"))

    def test_exact_key_longer_than_legacy_limit_resolves(self) -> None:
        key = "x" * 241
        chunk = _chunk("long", (key,), "conf/long.xml")
        index = TargetIndex([chunk], [])

        self.assertEqual(["long"], [hit.target_id for hit in index.lookup(key)])


if __name__ == "__main__":
    unittest.main()
