from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from pipeline.steps.step1_parse_ddl.ddl_parser import parse_ddl_file


class DdlCommentAuthorityTest(unittest.TestCase):
    def test_line_comments_are_preserved_but_not_promoted_to_descriptions(self):
        ddl = """\
-- Generated from static source evidence.
-- Source references: ORDERS
-- Operations: READ
CREATE TABLE ORDERS (
    STATUS_CD VARCHAR2(10) -- AMBIGUOUS_SOURCE
);
"""
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "schema.sql"
            path.write_text(ddl, encoding="utf-8")
            result = parse_ddl_file(path, db="db")

        self.assertEqual("", result.tables[0].description)
        self.assertEqual("", result.tables[0].description_source)
        self.assertIn("Source references: ORDERS", result.tables[0].comment)
        self.assertEqual("", result.columns[0].description)
        self.assertEqual("", result.columns[0].description_source)
        self.assertEqual("AMBIGUOUS_SOURCE", result.columns[0].comment)

    def test_formal_comment_on_remains_authoritative(self):
        ddl = """\
-- build metadata
CREATE TABLE ORDERS (STATUS_CD VARCHAR2(10));
COMMENT ON TABLE ORDERS IS '주문 처리 상태';
COMMENT ON COLUMN ORDERS.STATUS_CD IS '주문 상태 코드';
"""
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "schema.sql"
            path.write_text(ddl, encoding="utf-8")
            result = parse_ddl_file(path, db="db")

        self.assertEqual("주문 처리 상태", result.tables[0].description)
        self.assertEqual("ddl", result.tables[0].description_source)
        self.assertEqual("주문 상태 코드", result.columns[0].description)
        self.assertEqual("ddl", result.columns[0].description_source)

    def test_quoted_column_identity_is_preserved_for_case_sensitive_resolution(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "quoted.sql"
            path.write_text(
                'CREATE TABLE T ("Foo" NUMBER, "foo" NUMBER, BAR NUMBER);',
                encoding="utf-8",
            )

            result = parse_ddl_file(path, db="db", dialect="oracle")

        self.assertEqual(
            [("Foo", True), ("foo", True), ("BAR", False)],
            [(column.name, column.name_quoted) for column in result.columns],
        )


if __name__ == "__main__":
    unittest.main()
