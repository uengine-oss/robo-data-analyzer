from __future__ import annotations

import unittest

from pipeline.steps.step7_analyze_code.model_source_projection import (
    project_model_source_literals,
)


class ModelSourceProjectionTest(unittest.TestCase):
    def test_preserves_lines_and_projects_string_and_control_literals(self) -> None:
        source = (
            '137 | if (settle_dt == NULL) settle_dt = "00000000";\n'
            "138 | if (s[i] != ' ' && s[i] != '\\t' && s[i] != '\\0') return 0;"
        )
        projected = project_model_source_literals(source)

        self.assertEqual(source.count("\n"), projected.count("\n"))
        self.assertIn("[QUOTED_LITERAL value=00000000]", projected)
        self.assertIn("[QUOTED_LITERAL value= ]", projected)
        self.assertIn("[QUOTED_LITERAL value=<CHARACTER TAB>]", projected)
        self.assertIn("[QUOTED_LITERAL value=<CHARACTER NUL>]", projected)
        self.assertNotIn('"00000000"', projected)
        self.assertNotIn("'\\t'", projected)

    def test_sql_doubled_quote_content_is_preserved_without_delimiters(self) -> None:
        source = "1 | WHERE name = 'O''Brien'"
        self.assertEqual(
            "1 | WHERE name = [QUOTED_LITERAL value=O''Brien]",
            project_model_source_literals(source),
        )

    def test_unmatched_comment_apostrophe_is_left_unchanged(self) -> None:
        source = "1 | /* caller's policy */"
        self.assertEqual(source, project_model_source_literals(source))


if __name__ == "__main__":
    unittest.main()
