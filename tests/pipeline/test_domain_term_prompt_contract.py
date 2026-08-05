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


if __name__ == "__main__":
    unittest.main()
