"""Domain-term prompt byte/content contracts."""

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SUMMARY_PROMPTS = (
    ROOT / "llm" / "prompts" / "framework" / "code_summary.yaml",
    ROOT / "llm" / "prompts" / "dbms" / "code_summary_batch.yaml",
)


class DomainTermPromptContractTest(unittest.TestCase):
    def test_both_strategies_state_cap_not_target_and_priority_selection(self) -> None:
        for path in SUMMARY_PROMPTS:
            with self.subTest(path=path.name):
                prompt = path.read_text(encoding="utf-8")
                self.assertIn("0~10개", prompt)
                self.assertIn("10개는 목표가 아니라 마지노선", prompt)
                self.assertIn("상위 10개", prompt)
                self.assertIn("10번째에서 배열을 닫", prompt)
                self.assertIn("같은 우선순위", prompt)
                self.assertIn("먼저 등장", prompt)

    def test_both_strategies_include_normal_failure_and_boundary_examples(self) -> None:
        for path in SUMMARY_PROMPTS:
            with self.subTest(path=path.name):
                prompt = path.read_text(encoding="utf-8")
                self.assertIn("올바른 예시", prompt)
                self.assertIn("잘못된 예시", prompt)
                self.assertIn("10개 경계 예시", prompt)
                self.assertIn("SK-VAN", prompt)
                self.assertIn("PG사 정산 대조 결과 상세 코드", prompt)
                self.assertIn("후보가 없으면 `[]`", prompt)

    def test_old_unbounded_contract_cannot_reappear(self) -> None:
        forbidden = (
            "정상 표제어를 버리지 않는다",
            "정상 항목 개수 하드 제한은 두지 않는다",
            "11개 이상 정상 응답도",
            "실제 표제어 모두",
        )
        paths = (*SUMMARY_PROMPTS, ROOT / "specs" / "050-bounded-domain-terms" / "spec.md")
        for path in paths:
            with self.subTest(path=path.name):
                text = path.read_text(encoding="utf-8")
                for phrase in forbidden:
                    self.assertNotIn(phrase, text)

    def test_core_fallback_forbids_search_fields_and_has_small_budget(self) -> None:
        prompt = (ROOT / "llm" / "prompts" / "framework" / "code_summary_core.yaml").read_text(
            encoding="utf-8"
        )
        self.assertIn("max_tokens: 600", prompt)
        self.assertIn("`search_text`와 `domain_terms`는 절대 생성하지 않는다", prompt)
        self.assertIn("즉시 JSON을 닫는다", prompt)


if __name__ == "__main__":
    unittest.main()
