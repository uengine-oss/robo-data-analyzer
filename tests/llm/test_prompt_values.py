from __future__ import annotations

import unittest

from llm.runners.prompt_values import _safe_format


class SafeFormatTest(unittest.TestCase):
    def test_nested_json_value_is_opaque(self) -> None:
        evidence = '{"R1":{"direct_effects":{"calls":[],"returns":[]}}}'

        rendered = _safe_format("evidence:\n{value}\n", {"value": evidence})

        self.assertEqual(f"evidence:\n{evidence}\n", rendered)

    def test_template_brace_escapes_are_unescaped_without_substitution(self) -> None:
        rendered = _safe_format(
            'shape={{"value":"{value}"}} literal={{value}}',
            {"value": "injected"},
        )

        self.assertEqual(
            'shape={"value":"injected"} literal={value}',
            rendered,
        )

    def test_unknown_brace_text_is_preserved(self) -> None:
        self.assertEqual(
            "free {unknown:key} text",
            _safe_format("free {unknown:key} text", {"known": "value"}),
        )


if __name__ == "__main__":
    unittest.main()
