"""Canonical prompt contracts expose only bounded semantic leaves."""
from pathlib import Path
import unittest

from tests.pipeline.semantic_prompt_paths import contract_path, load_semantic_rule


ROOT = Path(__file__).resolve().parents[2]
SUMMARY_CASES = (
    ("framework", "code_summary"),
    ("dbms", "code_summary_node"),
)


class CanonicalSemanticPromptContractTest(unittest.TestCase):
    @staticmethod
    def _expanded(rule: dict) -> str:
        text = rule["user_template"]
        for key, value in rule["static_values"].items():
            text = text.replace("{" + key + "}", value)
        return text

    def test_summary_strategies_share_one_grounding_contract(self) -> None:
        shared = contract_path("summary", "summary_grounding_contract.md").read_text(
            encoding="utf-8",
        ).strip()
        for strategy, rule_name in SUMMARY_CASES:
            with self.subTest(strategy=strategy):
                rule = load_semantic_rule(strategy, rule_name)
                self.assertEqual(shared, rule["static_values"]["summary_grounding_contract"])
                self.assertEqual(1, rule["user_template"].count("{summary_grounding_contract}"))

    def test_summary_wire_has_only_one_whole_summary_and_stereotype(self) -> None:
        for strategy, rule_name in SUMMARY_CASES:
            text = load_semantic_rule(strategy, rule_name)["user_template"]
            with self.subTest(strategy=strategy):
                self.assertNotIn("`slots`", text)
                self.assertIn("`stereotype`", text)
                self.assertIn("`summary`", text)
                self.assertNotIn("`questions`", text)
                self.assertNotIn("`domain_terms`", text)

    def test_removed_freeform_synthesis_contracts_do_not_reappear(self) -> None:
        for path in (
            ROOT / "pipeline/semantic_tasks/common/domain_terms_contract.md",
            ROOT / "pipeline/semantic_tasks/common/semantic_text_contract.md",
            ROOT / "pipeline/semantic_tasks/common/glossary_synthesis_contract.md",
            ROOT / "pipeline/semantic_tasks/framework/glossary_synthesis.yaml",
            ROOT / "pipeline/semantic_tasks/dbms/glossary_synthesis.yaml",
        ):
            with self.subTest(path=path):
                self.assertFalse(path.exists())

    def test_summary_contract_forbids_internal_identity_and_model_self_status(self) -> None:
        shared = contract_path("summary", "summary_grounding_contract.md").read_text(
            encoding="utf-8",
        )
        for marker in (
            "facts", "fact ID", "occurrence ID", "hash", "path",
            "grammar reference", "request-local 결합 key", "조건 극성",
            "소유 범위", "opaque 함수", "함수 이름을 보고",
            "null/empty/valid/success", "다른 언어의", "문자나 단어",
        ):
            self.assertIn(marker, shared)
        for retired in ("applicable_slots_json", "fact_refs", "`slots`", "`status`"):
            self.assertNotIn(retired, shared)

    def test_function_summary_contract_closes_noop_and_complete_flow_roles(self) -> None:
        shared = contract_path("summary", "function_summary_contract.md").read_text(
            encoding="utf-8",
        )
        for marker in (
            "target의 전체 책임을 한 흐름",
            "선행 검증, 조건별 처리",
            "사용되지 않는 파라미터",
            "output-only",
            "즉시 상수 반환",
            "호출자 문맥 부재를 이유로 낮추지 않는다",
        ):
            self.assertIn(marker, shared)
        for strategy, rule_name in SUMMARY_CASES:
            with self.subTest(strategy=strategy):
                rule = load_semantic_rule(strategy, rule_name)
                self.assertEqual(
                    shared.strip(),
                    rule["static_values"]["function_summary_contract"],
                )
                self.assertEqual(
                    1,
                    rule["user_template"].count("{function_summary_contract}"),
                )

    def test_model_visible_source_explains_literal_and_coordinate_projection(self) -> None:
        for strategy, rule_name in SUMMARY_CASES:
            text = self._expanded(load_semantic_rule(strategy, rule_name))
            with self.subTest(strategy=strategy):
                self.assertIn("절대 파일 줄번호 | 원문", text)
                self.assertNotIn("target_sha256", text)
                self.assertNotIn("QUOTED_LITERAL value=", text)

    def test_framework_stereotype_keeps_low_level_string_work_as_util(self) -> None:
        text = load_semantic_rule("framework", "code_summary")["user_template"]
        self.assertIn("저수준 자료구조·문자열 연산은 `Util`", text)
        self.assertIn("횡단 시스템 기능은 `Utility`", text)

    def test_shared_prompt_path_cannot_escape_prompts_root(self) -> None:
        loader = (ROOT / "llm/runners/prompt_rules.py").read_text(encoding="utf-8")
        self.assertIn("shared prompt value escapes prompts root", loader)

    def test_verified_transport_overrides_are_preserved(self) -> None:
        framework = load_semantic_rule("framework", "code_summary")
        dbms = load_semantic_rule("dbms", "code_summary_node")
        dbms_table = load_semantic_rule("dbms", "code_table_usage_meanings_batch")
        self.assertEqual("function_calling", framework["structured_output_method"])
        self.assertIsNone(dbms["structured_output_method"])
        self.assertEqual("function_calling", dbms_table["structured_output_method"])

    def test_semantic_prompts_keep_task_contract_decision_and_input_order(self) -> None:
        cases = (
            ("framework", "code_summary"),
            ("dbms", "code_summary_node"),
            ("framework", "code_table_usage_meanings"),
            ("dbms", "code_table_usage_meanings_batch"),
            ("framework", "table_description"),
            ("dbms", "table_description"),
        )
        for strategy, name in cases:
            text = self._expanded(load_semantic_rule(strategy, name))
            with self.subTest(strategy=strategy, rule=name):
                self.assertIn("[TASK", text)
                self.assertIn("[OUTPUT CONTRACT", text)
                self.assertIn("[DECISION", text)
                self.assertIn("[SELF CHECK]", text)
                self.assertIn("[INPUT", text)
                self.assertLess(text.index("[TASK"), text.rindex("[INPUT"))

    def test_rule_provider_prompts_are_physically_retired(self) -> None:
        retired = (
            "common/rule_leaf_contract.md",
            "framework/_rule_common.yaml",
            "framework/code_rules_examples.yaml",
            "dbms/_rule_common.yaml",
            "dbms/code_rules_examples_batch.yaml",
            "dbms/code_gate_rules_batch.yaml",
        )
        for relative in retired:
            with self.subTest(relative=relative):
                self.assertFalse((ROOT / "pipeline/semantic_tasks" / relative).exists())

    def test_summary_and_table_usage_keep_distinct_input_scopes(self) -> None:
        summary = self._expanded(load_semantic_rule("framework", "code_summary"))
        table = self._expanded(load_semantic_rule(
            "framework", "code_table_usage_meanings",
        ))
        self.assertIn("linked_callee_outcomes_json", summary)
        self.assertIn("linked_external_sources_json", summary)
        self.assertIn("table_description", table)
        self.assertNotIn("table_description", summary)
        self.assertNotIn("linked_callee_outcomes_json", table)
        self.assertNotIn("linked_external_sources_json", table)

    def test_column_meaning_is_deferred_to_final_table_frame(self) -> None:
        for strategy, name in (
            ("framework", "code_table_usage_meanings"),
            ("dbms", "code_table_usage_meanings_batch"),
        ):
            text = self._expanded(load_semantic_rule(strategy, name))
            with self.subTest(strategy=strategy):
                self.assertIn("COLUMN", text)
                self.assertIn("만들지 않는다", text)
        final_table = self._expanded(load_semantic_rule(
            "framework", "table_description",
        ))
        self.assertIn("한 TABLE과 허용 COLUMN의 최종 의미", final_table)

    def test_table_usage_prompts_match_the_meaning_only_response_schema(self) -> None:
        cases = (
            ("framework", "code_table_usage_meanings"),
            ("dbms", "code_table_usage_meanings_batch"),
        )
        retired_response_wrappers = (
            "`results`",
            "mapped.function_table_usage_summary",
            "insufficient.disposition_reason_code",
            "semantic_not_derivable",
        )
        for strategy, name in cases:
            text = self._expanded(load_semantic_rule(strategy, name))
            with self.subTest(strategy=strategy):
                self.assertIn("FT key exact required map", text)
                self.assertIn("빈 문자열", text)
                for marker in retired_response_wrappers:
                    self.assertNotIn(marker, text)


if __name__ == "__main__":
    unittest.main()
