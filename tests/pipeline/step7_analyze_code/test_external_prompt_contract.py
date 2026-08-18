"""Prompt-side external evidence contract (spec 078 S4/T401-T402).

The common system prefix must stay byte-identical across parallel requests
(KV cache), so external evidence may appear only in the user template.  The
system commentary must describe the 078 schema (matched_key, no call-meaning
claims) and not the removed 075 vocabulary.
"""
from __future__ import annotations

from pathlib import Path
import unittest

from llm.runners.prompt_rules import _load_rule
from llm.runners.prompt_values import _safe_format


PROMPTS_DIR = Path("llm/prompts/framework")
RULES = ("code_summary", "code_table_insights")


class ExternalPromptContractTest(unittest.TestCase):
    def test_external_evidence_lives_only_in_user_template(self) -> None:
        for rule_name in RULES:
            with self.subTest(rule=rule_name):
                rule = _load_rule(PROMPTS_DIR, rule_name)
                self.assertNotIn("{external_evidence}", rule.get("system", ""))
                self.assertIn("{external_evidence}", rule.get("user_template", ""))

    def test_system_prefix_is_static_and_byte_identical_across_loads(self) -> None:
        for rule_name in RULES:
            with self.subTest(rule=rule_name):
                first = _load_rule(PROMPTS_DIR, rule_name).get("system", "")
                second = _load_rule(PROMPTS_DIR, rule_name).get("system", "")
                self.assertEqual(first.encode("utf-8"), second.encode("utf-8"))
                # system 텍스트에 남은 미치환 placeholder 가 없어야 실행 간 byte 동일이 보장된다.
                self.assertEqual(first, _safe_format(first, {}))

    def test_system_describes_078_schema_not_075(self) -> None:
        system = _load_rule(PROMPTS_DIR, "code_summary").get("system", "")
        self.assertIn("external_evidence", system)
        self.assertIn("matched_key", system)
        for removed in ("CALLS_SQL", "USES_CONFIG", "MENTIONS"):
            self.assertNotIn(removed, system)

    def test_evidence_varies_only_the_user_message(self) -> None:
        rule = _load_rule(PROMPTS_DIR, "code_summary")
        template = rule.get("user_template", "")
        values = {
            "codebase_notes": "", "module_intent": "", "reads": "[]", "writes": "[]",
            "referenced_symbols": "[]", "callee_summaries": "", "id": "code:a",
            "signature": "a()", "code_body": "void a() {}",
        }
        left = _safe_format(template, {**values, "external_evidence": '[{"matched_key":"K1"}]'})
        right = _safe_format(template, {**values, "external_evidence": '[{"matched_key":"K2"}]'})
        self.assertNotEqual(left, right)
        self.assertIn('"matched_key":"K1"', left)
        self.assertNotIn("K2", left)


if __name__ == "__main__":
    unittest.main()
