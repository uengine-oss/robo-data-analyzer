"""Prompt-side linked external source contract.

The common system prefix stays byte-identical across parallel requests for KV
reuse.  Linked external source text and its one authoritative explanation live only in
the Summary user contract, avoiding a duplicate system instruction.
"""
from __future__ import annotations

from pathlib import Path
import unittest

from llm.runners.prompt_rules import _load_rule
from llm.runners.prompt_values import _safe_format


PROMPTS_DIR = Path("llm/prompts/framework")
RULES = ("code_summary", "code_table_usage_meanings")


class ExternalPromptContractTest(unittest.TestCase):
    def test_linked_external_sources_live_only_in_user_template(self) -> None:
        summary = _load_rule(PROMPTS_DIR, "code_summary")
        table = _load_rule(PROMPTS_DIR, "code_table_usage_meanings")
        self.assertNotIn("{linked_external_sources}", summary.get("system", ""))
        self.assertIn("{linked_external_sources}", summary.get("user_template", ""))
        self.assertNotIn("{linked_external_sources}", table.get("user_template", ""))

    def test_system_prefix_is_static_and_byte_identical_across_loads(self) -> None:
        for rule_name in RULES:
            with self.subTest(rule=rule_name):
                first = _load_rule(PROMPTS_DIR, rule_name).get("system", "")
                second = _load_rule(PROMPTS_DIR, rule_name).get("system", "")
                self.assertEqual(first.encode("utf-8"), second.encode("utf-8"))
                # system 텍스트에 남은 미치환 placeholder 가 없어야 실행 간 byte 동일이 보장된다.
                self.assertEqual(first, _safe_format(first, {}))

    def test_summary_contract_describes_live_external_packet_once(self) -> None:
        rule = _load_rule(PROMPTS_DIR, "code_summary")
        system = rule.get("system", "")
        user = rule.get("user_template", "")
        contract = rule["static_values"]["summary_grounding_contract"]
        self.assertNotIn("linked_external_sources", system)
        self.assertIn("linked_external_sources_json", user)
        self.assertIn("request-local 결합 key", contract)
        for removed in ("CALLS_SQL", "USES_CONFIG", "MENTIONS"):
            self.assertNotIn(removed, system + user + contract)

    def test_linked_external_source_varies_only_the_user_message(self) -> None:
        rule = _load_rule(PROMPTS_DIR, "code_summary")
        template = rule.get("user_template", "")
        values = {
            "structural_profile": "{}",
            "linked_callee_outcomes": "[]",
            "code_body": "void a() {}",
        }
        left = _safe_format(template, {**values, "linked_external_sources": '[{"text":"first text"}]'})
        right = _safe_format(template, {**values, "linked_external_sources": '[{"text":"second text"}]'})
        self.assertNotEqual(left, right)
        self.assertIn('"text":"first text"', left)
        self.assertNotIn("second text", left)


if __name__ == "__main__":
    unittest.main()
