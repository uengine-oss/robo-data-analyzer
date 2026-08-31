"""Canonical prompt contracts expose only bounded semantic leaves."""
from pathlib import Path
import unittest

from llm.runners.yaml_llm_runner import _load_rule


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

    def test_summary_strategies_share_one_semantic_frame_contract(self) -> None:
        shared = (ROOT / "llm/prompts/common/semantic_frame_contract.md").read_text(
            encoding="utf-8",
        ).strip()
        for strategy, rule_name in SUMMARY_CASES:
            with self.subTest(strategy=strategy):
                rule = _load_rule(ROOT / "llm/prompts" / strategy, rule_name)
                self.assertEqual(shared, rule["static_values"]["semantic_frame_contract"])
                self.assertEqual(1, rule["user_template"].count("{semantic_frame_contract}"))

    def test_summary_wire_has_one_detail_map_and_one_whole_summary(self) -> None:
        for strategy, rule_name in SUMMARY_CASES:
            text = _load_rule(ROOT / "llm/prompts" / strategy, rule_name)["user_template"]
            with self.subTest(strategy=strategy):
                self.assertIn("`slots`", text)
                self.assertIn("`stereotype`", text)
                self.assertIn("`summary`", text)
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

    def test_frame_contract_forbids_internal_identity_and_model_self_status(self) -> None:
        shared = (ROOT / "llm/prompts/common/semantic_frame_contract.md").read_text(
            encoding="utf-8",
        )
        for marker in (
            "applicable_slots_json", "facts", "fact_refs", "증거 메타데이터",
            "식별자", "조건 극성", "형제", "소유 범위",
            "의미 충분성은 모델이 판정하지 않는다",
        ):
            self.assertIn(marker, shared)
        self.assertNotIn("`status`", shared)

    def test_function_summary_contract_closes_noop_and_complete_flow_roles(self) -> None:
        shared = (ROOT / "llm/prompts/common/function_summary_contract.md").read_text(
            encoding="utf-8",
        )
        for marker in (
            "FUNCTION.GUARDS_AND_BRANCHES",
            "FUNCTION.PROCESSING_EFFECTS",
            "다른 FUNCTION role이 소유한 입력·분기·처리·결과 세부를 여기서 다시",
            "사용되지 않는 파라미터",
            "output-only",
            "즉시 상수 반환",
            "호출자 문맥 부재를 이유로 낮추지 않는다",
        ):
            self.assertIn(marker, shared)
        for strategy, rule_name in SUMMARY_CASES:
            with self.subTest(strategy=strategy):
                rule = _load_rule(ROOT / "llm/prompts" / strategy, rule_name)
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
            text = self._expanded(_load_rule(
                ROOT / "llm/prompts" / strategy, rule_name,
            ))
            with self.subTest(strategy=strategy):
                self.assertIn("절대 파일 줄번호 | 원문", text)
                self.assertNotIn("target_sha256", text)
                self.assertNotIn("QUOTED_LITERAL value=", text)

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
        dbms = _load_rule(ROOT / "llm/prompts/dbms", "code_summary_node")
        dbms_table = _load_rule(
            ROOT / "llm/prompts/dbms", "code_table_usage_meanings_batch",
        )
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
            text = self._expanded(_load_rule(ROOT / "llm/prompts" / strategy, name))
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
                self.assertFalse((ROOT / "llm/prompts" / relative).exists())

    def test_summary_and_table_usage_keep_distinct_input_scopes(self) -> None:
        summary = self._expanded(_load_rule(
            ROOT / "llm/prompts/framework", "code_summary",
        ))
        table = self._expanded(_load_rule(
            ROOT / "llm/prompts/framework", "code_table_usage_meanings",
        ))
        self.assertIn("linked_callee_outcomes_json", summary)
        self.assertIn("linked_external_source_json", summary)
        self.assertIn("table_description", table)
        self.assertNotIn("table_description", summary)
        self.assertNotIn("linked_callee_outcomes_json", table)
        self.assertNotIn("linked_external_source_json", table)

    def test_column_meaning_is_deferred_to_final_table_frame(self) -> None:
        for strategy, name in (
            ("framework", "code_table_usage_meanings"),
            ("dbms", "code_table_usage_meanings_batch"),
        ):
            text = self._expanded(_load_rule(ROOT / "llm/prompts" / strategy, name))
            with self.subTest(strategy=strategy):
                self.assertIn("COLUMN", text)
                self.assertIn("만들지 않는다", text)
        final_table = self._expanded(_load_rule(
            ROOT / "llm/prompts/framework", "table_description",
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
            text = self._expanded(_load_rule(
                ROOT / "llm/prompts" / strategy, name,
            ))
            with self.subTest(strategy=strategy):
                self.assertIn("FT key exact required map", text)
                self.assertIn("빈 문자열", text)
                for marker in retired_response_wrappers:
                    self.assertNotIn(marker, text)


if __name__ == "__main__":
    unittest.main()
