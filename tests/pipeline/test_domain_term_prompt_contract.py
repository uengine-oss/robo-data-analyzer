"""Canonical prompt contracts expose only bounded semantic leaves."""
from pathlib import Path
import unittest

from llm.runners.yaml_llm_runner import _load_rule


ROOT = Path(__file__).resolve().parents[2]
SUMMARY_CASES = (
    ("framework", "code_summary"),
    ("dbms", "code_summary_batch"),
)


class CanonicalSemanticPromptContractTest(unittest.TestCase):
    @staticmethod
    def _expanded(rule: dict) -> str:
        text = rule["user_template"]
        for key, value in rule["static_values"].items():
            text = text.replace("{" + key + "}", value)
        return text

    def test_summary_strategies_share_one_semantic_frame_contract(self) -> None:
        shared = (ROOT / "llm/prompts/common/semantic_frame_contract.md").read_text(
            encoding="utf-8",
        ).strip()
        for strategy, rule_name in SUMMARY_CASES:
            with self.subTest(strategy=strategy):
                rule = _load_rule(ROOT / "llm/prompts" / strategy, rule_name)
                self.assertEqual(shared, rule["static_values"]["semantic_frame_contract"])
                self.assertEqual(1, rule["user_template"].count("{semantic_frame_contract}"))

    def test_summary_wire_is_slots_plus_stereotype_only(self) -> None:
        for strategy, rule_name in SUMMARY_CASES:
            text = _load_rule(ROOT / "llm/prompts" / strategy, rule_name)["user_template"]
            with self.subTest(strategy=strategy):
                self.assertIn("`slots`", text)
                self.assertIn("`stereotype`", text)
                self.assertNotIn("`summary`", text)
                self.assertNotIn("`questions`", text)
                self.assertNotIn("`domain_terms`", text)

    def test_removed_freeform_synthesis_contracts_do_not_reappear(self) -> None:
        for path in (
            ROOT / "llm/prompts/common/domain_terms_contract.md",
            ROOT / "llm/prompts/common/semantic_text_contract.md",
            ROOT / "llm/prompts/common/glossary_synthesis_contract.md",
            ROOT / "llm/prompts/framework/glossary_synthesis.yaml",
            ROOT / "llm/prompts/dbms/glossary_synthesis.yaml",
        ):
            with self.subTest(path=path):
                self.assertFalse(path.exists())

    def test_frame_contract_forbids_identifier_and_ownership_invention(self) -> None:
        shared = (ROOT / "llm/prompts/common/semantic_frame_contract.md").read_text(
            encoding="utf-8",
        )
        for marker in (
            "APPLICABLE_SLOTS", "evidence_refs", "missing_context",
            "물리 식별자", "callee 내부", "조건 반전", "형제", "다른 table",
        ):
            self.assertIn(marker, shared)

    def test_model_visible_source_explains_literal_and_coordinate_projection(self) -> None:
        for strategy, rule_name in SUMMARY_CASES:
            text = self._expanded(_load_rule(
                ROOT / "llm/prompts" / strategy, rule_name,
            ))
            with self.subTest(strategy=strategy):
                self.assertIn("QUOTED_LITERAL value=", text)
                self.assertIn("결정론 투영", text)
                self.assertIn("절대 줄 좌표", text)

    def test_framework_stereotype_keeps_low_level_string_work_as_util(self) -> None:
        text = _load_rule(
            ROOT / "llm/prompts/framework", "code_summary",
        )["user_template"]
        self.assertIn("저수준 자료구조·문자열 연산은 `Util`", text)
        self.assertIn("횡단 시스템 기능은 `Utility`", text)

    def test_shared_prompt_path_cannot_escape_prompts_root(self) -> None:
        loader = (ROOT / "llm/runners/prompt_rules.py").read_text(encoding="utf-8")
        self.assertIn("shared prompt value escapes prompts root", loader)

    def test_verified_transport_overrides_are_preserved(self) -> None:
        framework = _load_rule(ROOT / "llm/prompts/framework", "code_summary")
        dbms = _load_rule(ROOT / "llm/prompts/dbms", "code_summary_batch")
        dbms_table = _load_rule(
            ROOT / "llm/prompts/dbms", "code_table_insights_batch",
        )
        self.assertEqual("function_calling", framework["structured_output_method"])
        self.assertIsNone(dbms["structured_output_method"])
        self.assertEqual("function_calling", dbms_table["structured_output_method"])

    def test_semantic_prompts_keep_task_contract_decision_and_input_order(self) -> None:
        cases = (
            ("framework", "code_summary"),
            ("dbms", "code_summary_batch"),
            ("framework", "code_table_insights"),
            ("dbms", "code_table_insights_batch"),
            ("framework", "table_description"),
            ("dbms", "table_description"),
        )
        for strategy, name in cases:
            text = self._expanded(_load_rule(ROOT / "llm/prompts" / strategy, name))
            with self.subTest(strategy=strategy, rule=name):
                self.assertIn("[TASK", text)
                self.assertIn("[OUTPUT CONTRACT", text)
                self.assertIn("[DECISION", text)
                self.assertIn("[SELF CHECK]", text)
                self.assertIn("[INPUT", text)
                self.assertLess(text.index("[TASK"), text.rindex("[INPUT"))

    def test_rule_prompts_share_one_exact_leaf_contract(self) -> None:
        shared = (ROOT / "llm/prompts/common/rule_leaf_contract.md").read_text(
            encoding="utf-8",
        ).strip()
        for strategy, name in (
            ("framework", "code_rules_examples"),
            ("dbms", "code_rules_examples_batch"),
        ):
            rule = _load_rule(ROOT / "llm/prompts" / strategy, name)
            with self.subTest(strategy=strategy):
                self.assertEqual(shared, rule["static_values"]["rule_leaf_contract"])
                self.assertIn("semantic slot meaning", rule["user_template"])

    def test_summary_and_table_usage_keep_distinct_evidence_scopes(self) -> None:
        summary = self._expanded(_load_rule(
            ROOT / "llm/prompts/framework", "code_summary",
        ))
        table = self._expanded(_load_rule(
            ROOT / "llm/prompts/framework", "code_table_insights",
        ))
        self.assertIn("effective_reads_json", summary)
        self.assertIn("CALLEE_INTERFACES", summary)
        self.assertIn("direct_reads_json", table)
        self.assertIn("direct_data_object_usages_json", table)
        self.assertNotIn("effective_reads_json", table)
        self.assertNotIn("CALLEE_INTERFACES", table)

    def test_column_meaning_is_deferred_to_final_table_frame(self) -> None:
        for strategy, name in (
            ("framework", "code_table_insights"),
            ("dbms", "code_table_insights_batch"),
        ):
            text = self._expanded(_load_rule(ROOT / "llm/prompts" / strategy, name))
            with self.subTest(strategy=strategy):
                self.assertIn("COLUMN", text)
                self.assertIn("만들지 않는다", text)
        final_table = self._expanded(_load_rule(
            ROOT / "llm/prompts/framework", "table_description",
        ))
        self.assertIn("TABLE/COLUMN dynamic semantic slots", final_table)


if __name__ == "__main__":
    unittest.main()
