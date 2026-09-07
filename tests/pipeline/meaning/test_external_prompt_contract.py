"""Prompt-side linked external source contract.

The common system prefix stays byte-identical across parallel requests for KV
reuse.  Linked external source text and its one authoritative explanation live only in
the Summary user contract, avoiding a duplicate system instruction.
"""
from __future__ import annotations

import unittest

from llm.runners.prompt_values import _safe_format
from tests.pipeline.semantic_prompt_paths import load_semantic_rule


RULES = ("code_summary", "code_table_usage_meanings")


class ExternalPromptContractTest(unittest.TestCase):
    def test_linked_external_sources_live_only_in_user_template(self) -> None:
        summary = load_semantic_rule("framework", "code_summary")
        table = load_semantic_rule("framework", "code_table_usage_meanings")
        self.assertNotIn("{summary_context}", summary.get("system", ""))
        self.assertIn("{summary_context}", summary.get("user_template", ""))
        self.assertNotIn("{summary_context}", table.get("user_template", ""))

    def test_system_prefix_is_static_and_byte_identical_across_loads(self) -> None:
        for rule_name in RULES:
            with self.subTest(rule=rule_name):
                first = load_semantic_rule("framework", rule_name).get("system", "")
                second = load_semantic_rule("framework", rule_name).get("system", "")
                self.assertEqual(first.encode("utf-8"), second.encode("utf-8"))
                # system 텍스트에 남은 미치환 placeholder 가 없어야 실행 간 byte 동일이 보장된다.
                self.assertEqual(first, _safe_format(first, {}))

    def test_summary_contract_describes_live_external_packet_once(self) -> None:
        rule = load_semantic_rule("framework", "code_summary")
        system = rule.get("system", "")
        user = rule.get("user_template", "")
        contract = rule["static_values"]["summary_grounding_contract"]
        self.assertNotIn("LINKED PROJECT CONTRACTS", system)
        self.assertIn("{summary_context}", user)
        self.assertIn("LINKED PROJECT CONTRACTS", contract)
        for removed in ("CALLS_SQL", "USES_CONFIG", "MENTIONS"):
            self.assertNotIn(removed, system + user + contract)

    def test_linked_external_source_varies_only_the_user_message(self) -> None:
        rule = load_semantic_rule("framework", "code_summary")
        template = rule.get("user_template", "")
        values = {
            "code_body": "void a() {}",
        }
        left = _safe_format(template, {
            **values,
            "summary_context": '## [LINKED PROJECT CONTRACTS]\n[{"text":"first text"}]',
        })
        right = _safe_format(template, {
            **values,
            "summary_context": '## [LINKED PROJECT CONTRACTS]\n[{"text":"second text"}]',
        })
        self.assertNotEqual(left, right)
        self.assertIn('"text":"first text"', left)
        self.assertNotIn("second text", left)


if __name__ == "__main__":
    unittest.main()
