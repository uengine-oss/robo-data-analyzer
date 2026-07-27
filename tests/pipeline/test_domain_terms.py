from types import SimpleNamespace
import unittest

from pydantic import ValidationError

from pipeline.steps.step8_post_processing.build_glossary_step import _collect_terms
from pipeline.models.code_analysis_output import BatchSummaryItem, DomainTermItem, SummaryOutput
from graph.schema.domain_terms import dump_domain_terms, load_domain_terms
from pipeline.steps.step7_analyze_code.domain_terms import normalize_domain_terms


def _item(term: str, meaning: str, evidence: str | None = None) -> DomainTermItem:
    return DomainTermItem(term=term, meaning=meaning, evidence=evidence or term)


class DomainTermsTest(unittest.TestCase):
    def test_ten_is_allowed_but_eleven_is_rejected_everywhere(self) -> None:
        self.assertEqual(SummaryOutput(summary="요약", search_text="검색 설명").domain_terms, [])
        output = SummaryOutput(
            summary="요약",
            search_text="검색 설명",
            domain_terms=[
                {"term": f"TERM-{i}", "meaning": "뜻", "evidence": f"TERM-{i}"}
                for i in range(10)
            ],
        )
        self.assertEqual(len(output.domain_terms), 10)
        batch_output = BatchSummaryItem(
            node_key="1-2",
            summary="요약",
            search_text="검색 설명",
            domain_terms=[
                {"term": f"TERM-{i}", "meaning": "뜻", "evidence": f"TERM-{i}"}
                for i in range(10)
            ],
        )
        self.assertEqual(len(batch_output.domain_terms), 10)

        eleven = [
            {"term": f"TERM-{i}", "meaning": "뜻", "evidence": f"TERM-{i}"}
            for i in range(11)
        ]
        with self.assertRaises(ValidationError):
            SummaryOutput(summary="요약", search_text="검색 설명", domain_terms=eleven)
        with self.assertRaises(ValidationError):
            BatchSummaryItem(
                node_key="1-2",
                summary="요약",
                search_text="검색 설명",
                domain_terms=eleven,
            )
        with self.assertRaisesRegex(ValueError, "exceed maximum 10"):
            normalize_domain_terms(
                [_item(f"TERM-{i}", f"뜻-{i}") for i in range(11)],
                source_text=" ".join(f"TERM-{i}" for i in range(11)),
                code_id="code:many",
                component="test",
            )
        with self.assertRaisesRegex(ValueError, "exceed maximum 10"):
            dump_domain_terms({f"TERM-{i}": f"뜻-{i}" for i in range(11)})

    def test_generated_suffix_chain_is_rejected_as_not_in_source(self) -> None:
        result = normalize_domain_terms(
            [
                _item("MESH", "메시지 교환 규격"),
                _item("MESH 처리 결과 상세", "생성된 조합어"),
                _item("MESH 처리 결과 상세 코드", "생성된 조합어"),
            ],
            source_text="/* MESH 메시지 교환 */",
            code_id="code:runaway",
            component="test",
        )
        self.assertEqual(result, {"MESH": "메시지 교환 규격"})

    def test_normalize_keeps_priority_order_and_drops_ungrounded_duplicate(self) -> None:
        result = normalize_domain_terms(
            [_item("EXG-X", "교환 규격"), _item("EXG-X", "중복"), _item("MESH-X", "없는 말")],
            source_text="/* EXG-X 연동 */",
            code_id="code:test",
            component="test",
        )
        self.assertEqual(result, {"EXG-X": "교환 규격"})

    def test_source_grounded_candidates_are_not_deleted_by_identifier_shape(self) -> None:
        result = normalize_domain_terms(
            [
                _item("asset_id", "자산 식별자"),
                _item("EXG", "메시지 교환 규격"),
            ],
            source_text="asset_id /* EXG 메시지 교환 규격 */",
            code_id="code:test",
            component="test",
        )
        self.assertEqual(
            result,
            {"asset_id": "자산 식별자", "EXG": "메시지 교환 규격"},
        )

    def test_evidence_must_be_an_exact_source_excerpt_containing_term(self) -> None:
        result = normalize_domain_terms(
            [
                _item("EXG", "교환 규격", "EXG 교환 규격"),
                _item("MESH", "메시지망", "다른 문장"),
                _item("WIRE", "전송망", "전송망 설명"),
            ],
            source_text="/* EXG 교환 규격 */ MESH /* 전송망 설명 */ WIRE",
            code_id="code:evidence",
            component="test",
        )
        self.assertEqual(result, {"EXG": "교환 규격"})

    def test_json_codec_round_trips_delimiters_without_text_parsing(self) -> None:
        terms = {"A=B": "조건 x=1; y=2"}
        self.assertEqual(load_domain_terms(dump_domain_terms(terms)), terms)

    def test_glossary_collector_consumes_structured_terms(self) -> None:
        node = SimpleNamespace(domain_terms={"EXG-X": "교환 규격"})
        self.assertEqual(_collect_terms([node])["exg-x"]["meanings"], ["교환 규격"])


if __name__ == "__main__":
    unittest.main()
