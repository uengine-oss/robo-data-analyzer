"""Canonical summary prompt contracts."""

from pathlib import Path
import unittest

from llm.runners.yaml_llm_runner import _load_rule


ROOT = Path(__file__).resolve().parents[2]
SUMMARY_CASES = (
    (ROOT / "llm" / "prompts" / "framework", "code_summary"),
    (ROOT / "llm" / "prompts" / "dbms", "code_summary_batch"),
)


class CanonicalSummaryPromptContractTest(unittest.TestCase):
    @staticmethod
    def _expanded(rule: dict) -> str:
        text = rule["user_template"]
        for key, value in rule["static_values"].items():
            text = text.replace("{" + key + "}", value)
        return text

    def test_summary_has_one_shared_semantic_contract(self) -> None:
        shared = (
            ROOT / "llm" / "prompts" / "common" / "summary_contract.md"
        ).read_text(encoding="utf-8").strip()

        for directory, rule_name in SUMMARY_CASES:
            with self.subTest(rule=rule_name):
                rule = _load_rule(directory, rule_name)
                self.assertEqual(shared, rule["static_values"]["summary_contract"])
                self.assertNotIn("domain_terms_contract", rule["static_values"])
                self.assertEqual(
                    1, rule["user_template"].count("{summary_contract}")
                )
                self.assertNotIn("domain_term_evidence", rule["user_template"])

    def test_summary_wire_has_no_glossary_or_second_semantic_text(self) -> None:
        for directory, rule_name in SUMMARY_CASES:
            with self.subTest(rule=rule_name):
                text = _load_rule(directory, rule_name)["user_template"]
                self.assertIn("`summary`", text)
                self.assertIn("`stereotype`", text)
                self.assertIn("`questions`", text)
                self.assertNotIn("`domain_terms`", text)
                self.assertNotIn("`logical_name`", text)

    def test_removed_glossary_synthesis_prompts_do_not_reappear(self) -> None:
        for path in (
            ROOT / "llm" / "prompts" / "common" / "domain_terms_contract.md",
            ROOT / "llm" / "prompts" / "common" / "semantic_text_contract.md",
            ROOT / "llm" / "prompts" / "common" / "glossary_synthesis_contract.md",
            ROOT / "llm" / "prompts" / "framework" / "glossary_synthesis.yaml",
            ROOT / "llm" / "prompts" / "dbms" / "glossary_synthesis.yaml",
        ):
            with self.subTest(path=path):
                self.assertFalse(path.exists())

    def test_summary_contract_forbids_identifier_and_state_invention(self) -> None:
        shared = (
            ROOT / "llm" / "prompts" / "common" / "summary_contract.md"
        ).read_text(encoding="utf-8")
        self.assertIn("상태코드", shared)
        self.assertIn("추측하지 않는다", shared)
        self.assertIn("callee", shared)
        self.assertIn("JSON에서 제어 문자", shared)
        self.assertIn("괄호·따옴표·코드 리터럴을 덧붙이지 않는다", shared)
        self.assertIn("출력 버퍼나 반환값을 원문 형식으로 직접 조립", shared)
        self.assertIn("후속 사용처·업무상 이름", shared)
        self.assertIn("`DBMS_OUTPUT.PUT_LINE`에 전달해 호출", shared)
        self.assertIn("`C_DATA%NOTFOUND` 조건이 참이면", shared)

    def test_model_visible_source_explains_literal_projection(self) -> None:
        cases = (
            ("framework", "code_summary"),
            ("dbms", "code_summary_batch"),
            ("framework", "code_table_insights"),
            ("dbms", "code_table_insights_batch"),
        )
        for strategy, rule_name in cases:
            with self.subTest(strategy=strategy, rule=rule_name):
                text = self._expanded(_load_rule(
                    ROOT / "llm" / "prompts" / strategy, rule_name,
                ))
                self.assertIn("QUOTED_LITERAL value=", text)
                self.assertRegex(text, r"결정론\s+투영")
                self.assertIn("절대 줄 좌표", text)

    def test_framework_stereotype_contract_keeps_reusable_string_primitives_as_util(
        self,
    ) -> None:
        framework = _load_rule(
            ROOT / "llm" / "prompts" / "framework", "code_summary"
        )
        self.assertIn(
            "호출자 업무와 무관하게 재사용되는 저수준 판정·연산",
            framework["user_template"],
        )
        self.assertIn("`Helper`가 아니라 `Util`", framework["user_template"])

    def test_shared_prompt_path_cannot_escape_prompts_root(self) -> None:
        loader = (
            ROOT / "llm" / "runners" / "prompt_rules.py"
        ).read_text(encoding="utf-8")
        self.assertIn("shared prompt value escapes prompts root", loader)

    def test_framework_summary_owns_the_verified_transport_override(self) -> None:
        framework = _load_rule(
            ROOT / "llm" / "prompts" / "framework", "code_summary"
        )
        dbms = _load_rule(
            ROOT / "llm" / "prompts" / "dbms", "code_summary_batch"
        )
        self.assertEqual("function_calling", framework["structured_output_method"])
        self.assertIsNone(dbms["structured_output_method"])

        dbms_table = _load_rule(
            ROOT / "llm" / "prompts" / "dbms", "code_table_insights_batch"
        )
        self.assertEqual(
            "function_calling", dbms_table["structured_output_method"]
        )

    def test_semantic_prompts_have_one_human_reading_path(self) -> None:
        cases = (
            ("framework", "code_summary"),
            ("dbms", "code_summary_batch"),
            ("framework", "code_rules_examples"),
            ("dbms", "code_rules_examples_batch"),
            ("framework", "code_table_insights"),
            ("dbms", "code_table_insights_batch"),
            ("framework", "table_description"),
            ("dbms", "table_description"),
        )
        for strategy, rule_name in cases:
            with self.subTest(strategy=strategy, rule=rule_name):
                rule = _load_rule(
                    ROOT / "llm" / "prompts" / strategy, rule_name,
                )
                text = self._expanded(rule)
                for marker in (
                    "[TASK", "[OUTPUT CONTRACT", "[INPUT GUIDE",
                    "[DECISION", "[SELF CHECK]", "[INPUT",
                ):
                    self.assertIn(marker, text)
                self.assertLess(text.index("[TASK"), text.rindex("[INPUT"))
                self.assertRegex(text, r"(?:잘못|나쁨).*(?:좋음|올바른)|(?:잘못|나쁨)")

    def test_summary_and_table_usage_have_distinct_evidence_scopes(self) -> None:
        for strategy, summary_name, table_name in (
            ("framework", "code_summary", "code_table_insights"),
            ("dbms", "code_summary_batch", "code_table_insights_batch"),
        ):
            summary = self._expanded(_load_rule(
                ROOT / "llm" / "prompts" / strategy, summary_name,
            ))
            table = self._expanded(_load_rule(
                ROOT / "llm" / "prompts" / strategy, table_name,
            ))
            with self.subTest(strategy=strategy):
                self.assertIn("effective_reads_json", summary)
                self.assertIn("effective_writes_json", summary)
                self.assertIn("direct_reads_json", table)
                self.assertIn("direct_writes_json", table)
                self.assertNotIn("effective_reads_json", table)
                self.assertNotIn("SURROUNDING_CONTEXT", table)
                self.assertNotIn("CALLEE_INTERFACES", table)

    def test_input_relations_are_named_in_the_contract(self) -> None:
        summary = self._expanded(_load_rule(
            ROOT / "llm" / "prompts" / "framework", "code_summary",
        ))
        final_table = self._expanded(_load_rule(
            ROOT / "llm" / "prompts" / "framework", "table_description",
        ))
        for relation in (
            "TARGET_REFERENCES_DEFINITION",
            "TARGET_REFER_TO_EXTERNAL_CHUNK",
            "TARGET_CALLS_CALLEE",
            "TARGET_ACCESSES_DATA_OBJECT",
        ):
            self.assertIn(relation, summary)
        self.assertIn("CODE_NODE_USES_TABLE", final_table)
        self.assertIn("CODE_NODE_USES_COLUMN", final_table)

    def test_column_meaning_is_generated_once_from_final_candidate(self) -> None:
        for strategy, table_name in (
            ("framework", "code_table_insights"),
            ("dbms", "code_table_insights_batch"),
        ):
            table = self._expanded(_load_rule(
                ROOT / "llm" / "prompts" / strategy, table_name,
            ))
            with self.subTest(strategy=strategy):
                self.assertIn("한 번만 생성", table)
                self.assertIn("`columns` 필드를 중복 생성하지", table)
        final_table = self._expanded(_load_rule(
            ROOT / "llm" / "prompts" / "framework", "table_description",
        ))
        self.assertIn("sibling DDL column", final_table)
        self.assertIn("X candidate", final_table)


if __name__ == "__main__":
    unittest.main()
