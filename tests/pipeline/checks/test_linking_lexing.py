from __future__ import annotations

import unittest

from pipeline.checks.source_lexing import split_source_for_linking


class LinkingLexingTest(unittest.TestCase):
    def test_c_comments_are_removed_without_moving_lines_or_string_markers(self) -> None:
        source = (
            'call("https://service/path"); // inactive("orders")\n'
            '/* inactive("stock")\n'
            '   inactive("history") */\n'
            'active("shipping");\n'
        )
        live = split_source_for_linking(source, "c").live()
        self.assertEqual(len(source.splitlines()), len(live.splitlines()))
        self.assertIn('"https://service/path"', live.splitlines()[0])
        self.assertNotIn("orders", live)
        self.assertNotIn("stock", live)
        self.assertNotIn("history", live)
        self.assertIn('active("shipping")', live.splitlines()[3])

    def test_python_hash_comment_is_removed_but_triple_quoted_sql_is_preserved(self) -> None:
        source = (
            'query = """SELECT id\n'
            'FROM orders"""\n'
            '# query = "SELECT id FROM archive"\n'
        )
        live = split_source_for_linking(source, "python").live()
        self.assertIn("SELECT id", live)
        self.assertIn("FROM orders", live)
        self.assertNotIn("archive", live)

    def test_sql_comment_is_removed_but_quoted_dash_is_preserved(self) -> None:
        source = "SELECT '--' AS marker FROM orders; -- FROM archive\n"
        live = split_source_for_linking(source, "sql").live()
        self.assertIn("'--'", live)
        self.assertIn("FROM orders", live)
        self.assertNotIn("archive", live)


if __name__ == "__main__":
    unittest.main()
