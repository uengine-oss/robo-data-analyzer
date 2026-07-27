"""Domain-term prompt byte/content contracts."""

from pathlib import Path
import unittest

from llm.runners.yaml_llm_runner import _load_rule


ROOT = Path(__file__).resolve().parents[2]
SUMMARY_PROMPTS = (
    ROOT / "llm" / "prompts" / "framework" / "code_summary.yaml",
    ROOT / "llm" / "prompts" / "dbms" / "code_summary_batch.yaml",
)


class DomainTermPromptContractTest(unittest.TestCase):
    def test_both_strategies_state_cap_not_target_and_priority_selection(self) -> None:
        cases = (
            (ROOT / "llm" / "prompts" / "framework", "code_summary"),
            (ROOT / "llm" / "prompts" / "dbms", "code_summary_batch"),
        )
        for directory, rule_name in cases:
            with self.subTest(rule=rule_name):
                prompt = _load_rule(directory, rule_name)["static_values"]["domain_terms_contract"]
                self.assertIn("0~10개", prompt)
                self.assertIn("10개는 목표가 아니라 마지노선", prompt)
                self.assertIn("상위 10개", prompt)
                self.assertIn("10번째에서 배열을 닫", prompt)
                self.assertIn("같은 우선순위", prompt)
                self.assertIn("먼저 등장", prompt)

    def test_both_strategies_include_normal_failure_and_boundary_examples(self) -> None:
        cases = (
            (ROOT / "llm" / "prompts" / "framework", "code_summary"),
            (ROOT / "llm" / "prompts" / "dbms", "code_summary_batch"),
        )
        for directory, rule_name in cases:
            with self.subTest(rule=rule_name):
                prompt = _load_rule(directory, rule_name)["static_values"]["domain_terms_contract"]
                self.assertIn("올바른 예시", prompt)
                self.assertIn("잘못된 예시", prompt)
                self.assertIn("10개 경계 예시", prompt)
                self.assertIn("<원문의 외부망명>", prompt)
                self.assertIn("업무 결과 상세 코드", prompt)
                self.assertIn("후보가 없으면 `[]`", prompt)
                self.assertIn("`evidence`", prompt)
                self.assertIn("원문의 연속된 구절", prompt)
                self.assertIn("뜻을 설명하는 구절이 없으면", prompt)
                self.assertNotIn("SK-VAN", prompt)
                self.assertNotIn("PG사", prompt)

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

    def test_all_summary_variants_inject_one_shared_summary_rule_boundary(self) -> None:
        cases = (
            (ROOT / "llm" / "prompts" / "framework", "code_summary"),
            (ROOT / "llm" / "prompts" / "dbms", "code_summary_batch"),
        )
        shared = (ROOT / "llm" / "prompts" / "common" / "summary_contract.md").read_text(
            encoding="utf-8"
        ).strip()
        for directory, rule_name in cases:
            with self.subTest(rule=rule_name):
                rule = _load_rule(directory, rule_name)
                self.assertEqual(shared, rule["static_values"]["summary_contract"])
                self.assertIn("{summary_contract}", rule["user_template"])

    def test_common_prompts_do_not_override_summary_rule_boundary(self) -> None:
        shared = (ROOT / "llm" / "prompts" / "common" / "summary_contract.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("무엇을 왜 하고 무엇을 남기는지", shared)
        self.assertIn("핵심 예외·대상 없음·fallback·항상 정상 반환", shared)
        self.assertIn("`search_text`", shared)
        self.assertIn("`rules/examples`", shared)
        for strategy in ("framework", "dbms"):
            with self.subTest(strategy=strategy):
                common = (ROOT / "llm" / "prompts" / strategy / "_common.yaml").read_text(
                    encoding="utf-8"
                )
                self.assertIn("summary", common)
                self.assertIn("search_text", common)
                self.assertRegex(common, r"(?:서로 다른 책임|목적과 표현 책임)")
                self.assertNotIn("처리 축·대상·기준", common)
                self.assertNotIn("집계·처리 축", common)

    def test_shared_prompt_path_cannot_escape_prompts_root(self) -> None:
        loader = (ROOT / "llm" / "runners" / "prompt_rules.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("shared prompt value escapes prompts root", loader)


if __name__ == "__main__":
    unittest.main()
