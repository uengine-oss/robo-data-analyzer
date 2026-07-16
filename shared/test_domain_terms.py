from types import SimpleNamespace
import unittest

from pydantic import ValidationError

from analysis_flow.step7_post_processing.build_glossary_step import _collect_terms
from data_models.code_analysis_output import BatchSummaryItem, DomainTermItem, SummaryOutput
from shared.domain_terms import (
    dump_domain_terms,
    load_domain_terms,
    normalize_domain_terms,
)


def _item(term: str, meaning: str) -> DomainTermItem:
    return DomainTermItem(term=term, meaning=meaning)


class DomainTermsTest(unittest.TestCase):
    def test_ten_is_allowed_but_eleven_is_rejected_everywhere(self) -> None:
        self.assertEqual(SummaryOutput(summary="요약").domain_terms, [])
        output = SummaryOutput(
            summary="요약",
            domain_terms=[{"term": f"TERM-{i}", "meaning": "뜻"} for i in range(10)],
        )
        self.assertEqual(len(output.domain_terms), 10)
        batch_output = BatchSummaryItem(
            node_key="1-2",
            domain_terms=[{"term": f"TERM-{i}", "meaning": "뜻"} for i in range(10)],
        )
        self.assertEqual(len(batch_output.domain_terms), 10)

        eleven = [{"term": f"TERM-{i}", "meaning": "뜻"} for i in range(11)]
        with self.assertRaises(ValidationError):
            SummaryOutput(summary="요약", domain_terms=eleven)
        with self.assertRaises(ValidationError):
            BatchSummaryItem(node_key="1-2", domain_terms=eleven)
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
                _item("PG", "결제 중계망"),
                _item("PG사 정산 대조 결과 상세", "생성된 조합어"),
                _item("PG사 정산 대조 결과 상세 코드", "생성된 조합어"),
            ],
            source_text="/* PG 연동 정산 */",
            code_id="code:runaway",
            component="test",
        )
        self.assertEqual(result, {"PG": "결제 중계망"})

    def test_normalize_keeps_priority_order_and_drops_ungrounded_duplicate(self) -> None:
        result = normalize_domain_terms(
            [_item("SK-VAN", "결제망"), _item("SK-VAN", "중복"), _item("PG-X", "없는 말")],
            source_text="/* SK-VAN 연동 */",
            code_id="code:test",
            component="test",
        )
        self.assertEqual(result, {"SK-VAN": "결제망"})

    def test_normalize_rejects_implementation_identifiers_but_keeps_domain_acronym(self) -> None:
        result = normalize_domain_terms(
            [
                _item("member_id", "회원 식별자"),
                _item("RET_OK", "정상 반환"),
                _item("loadPg", "결제 호출 함수"),
                _item("VAN", "카드 승인 중계망"),
            ],
            source_text="member_id RET_OK loadPg /* VAN 카드 승인 중계망 */",
            code_id="code:test",
            component="test",
        )
        self.assertEqual(result, {"VAN": "카드 승인 중계망"})

    def test_json_codec_round_trips_delimiters_without_text_parsing(self) -> None:
        terms = {"A=B": "조건 x=1; y=2"}
        self.assertEqual(load_domain_terms(dump_domain_terms(terms)), terms)

    def test_glossary_collector_consumes_structured_terms(self) -> None:
        node = SimpleNamespace(domain_terms={"SK-VAN": "결제망"})
        self.assertEqual(_collect_terms([node])["sk-van"]["meanings"], ["결제망"])


if __name__ == "__main__":
    unittest.main()
