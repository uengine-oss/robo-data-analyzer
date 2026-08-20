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

    def test_quoted_search_path_preserves_schema_identity_and_comment_binding(self) -> None:
        ddl = '''\
SET search_path TO "App";
CREATE TABLE "Orders" ("Id" INTEGER);
COMMENT ON TABLE "App"."Orders" IS 'upper schema';
SET search_path TO "app";
CREATE TABLE "Orders" ("Id" INTEGER);
COMMENT ON TABLE "app"."Orders" IS 'lower schema';
'''
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "quoted-search-path.sql"
            path.write_text(ddl, encoding="utf-8")
            result = parse_ddl_file(path, db="db", dialect="postgres")

        self.assertEqual(
            [("App", True, "upper schema"), ("app", True, "lower schema")],
            [(table.schema, table.schema_quoted, table.description)
             for table in result.tables],
        )
        self.assertEqual(2, len({table.id for table in result.tables}))
        self.assertEqual(
            [("App", True), ("app", True)],
            [(schema.name, schema.name_quoted) for schema in result.schemas],
        )

    def test_quoted_and_unquoted_same_spelling_have_distinct_table_ids(self) -> None:
        ddl = 'CREATE TABLE FOO (ID INTEGER); CREATE TABLE "FOO" (ID INTEGER);'
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "quoted-table.sql"
            path.write_text(ddl, encoding="utf-8")
            result = parse_ddl_file(path, db="db", dialect="oracle")

        self.assertEqual([(False, "FOO"), (True, "FOO")], [
            (table.name_quoted, table.name) for table in result.tables
        ])
        self.assertEqual(2, len({table.id for table in result.tables}))

    def test_quoted_primary_key_and_comments_bind_only_exact_siblings(self) -> None:
        ddl = '''\
CREATE TABLE "T" (
    "Foo" NUMBER,
    "foo" NUMBER,
    CONSTRAINT PK_T PRIMARY KEY ("Foo")
);
COMMENT ON COLUMN "T"."Foo" IS 'upper column';
COMMENT ON COLUMN "T"."foo" IS 'lower column';
'''
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "quoted.sql"
            path.write_text(ddl, encoding="utf-8")
            result = parse_ddl_file(path, db="db", dialect="oracle")

        self.assertEqual(
            [
                ("Foo", True, "upper column"),
                ("foo", False, "lower column"),
            ],
            [
                (column.name, column.is_primary_key, column.description)
                for column in result.columns
            ],
        )

    def test_formal_comments_are_scoped_by_exact_schema_and_table_identity(self) -> None:
        ddl = '''\
CREATE TABLE "S1"."T" ("C" NUMBER);
CREATE TABLE "S2"."T" ("C" NUMBER);
COMMENT ON TABLE "S1"."T" IS 'first table';
COMMENT ON TABLE "S2"."T" IS 'second table';
COMMENT ON COLUMN "S1"."T"."C" IS 'first column';
COMMENT ON COLUMN "S2"."T"."C" IS 'second column';
'''
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "schemas.sql"
            path.write_text(ddl, encoding="utf-8")
            result = parse_ddl_file(path, db="db", dialect="oracle")

        self.assertEqual(
            [("S1", "first table"), ("S2", "second table")],
            [(table.schema, table.description) for table in result.tables],
        )
        self.assertEqual(
            [("db:db.Q~S1.Q~T.Q~C", "first column"),
             ("db:db.Q~S2.Q~T.Q~C", "second column")],
            [(column.id, column.description) for column in result.columns],
        )

    def test_sqlplus_separator_and_physical_words_do_not_rewrite_comment_literal(self) -> None:
        ddl = '''\
CREATE TABLE T (C VARCHAR2(10 BYTE)) TABLESPACE USERS NOLOGGING;
/
COMMENT ON COLUMN T.C IS 'TABLESPACE USERS; NOLOGGING is business text';
'''
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "oracle.sql"
            path.write_text(ddl, encoding="utf-8")
            result = parse_ddl_file(path, db="db", dialect="oracle")

        self.assertEqual(1, len(result.columns))
        self.assertEqual(
            "TABLESPACE USERS; NOLOGGING is business text",
            result.columns[0].description,
        )

    def test_oracle_physical_normalization_does_not_rewrite_identifiers_or_defaults(self) -> None:
        ddl = '''\
CREATE TABLE T (
    C VARCHAR2(10 BYTE) DEFAULT 'NOLOGGING',
    "NOLOGGING" VARCHAR2(20 CHAR) DEFAULT 'TABLESPACE USERS'
) TABLESPACE USERS STORAGE (INITIAL 1M) PCTFREE 10 NOLOGGING;
'''
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "oracle.sql"
            path.write_text(ddl, encoding="utf-8")
            result = parse_ddl_file(path, db="db", dialect="oracle")

        self.assertEqual(
            [
                ("C", False, "'NOLOGGING'"),
                ("NOLOGGING", True, "'TABLESPACE USERS'"),
            ],
            [
                (column.name, column.name_quoted, column.default_value)
                for column in result.columns
            ],
        )

    def test_sqlplus_slash_normalization_does_not_rewrite_multiline_default(self) -> None:
        ddl = "CREATE TABLE T (C VARCHAR2(20) DEFAULT 'first\n/\nlast');"
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "oracle.sql"
            path.write_text(ddl, encoding="utf-8")
            result = parse_ddl_file(path, db="db", dialect="oracle")

        self.assertEqual("'first\n/\nlast'", result.columns[0].default_value)

    def test_dialect_detection_ignores_marker_words_inside_default_literals(self) -> None:
        ddl = """\
CREATE TABLE `orders` (
    `id` INT,
    note TEXT DEFAULT 'TABLESPACE USERS NOLOGGING'
) ENGINE=InnoDB;
"""
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "mysql.sql"
            path.write_text(ddl, encoding="utf-8")
            result = parse_ddl_file(path, db="db")

        self.assertEqual(["orders"], [table.name for table in result.tables])
        self.assertEqual(
            "'TABLESPACE USERS NOLOGGING'",
            next(column.default_value for column in result.columns if column.name == "note"),
        )

    def test_oracle_physical_options_after_global_temporary_table_are_structural(self) -> None:
        ddl = """\
CREATE GLOBAL TEMPORARY TABLE SESSION_ITEMS (
    ITEM_ID NUMBER,
    NOTE VARCHAR2(10 BYTE) DEFAULT 'NOLOGGING'
) TABLESPACE USERS NOLOGGING;
"""
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "oracle.sql"
            path.write_text(ddl, encoding="utf-8")
            result = parse_ddl_file(path, db="db", dialect="oracle")

        self.assertEqual(["SESSION_ITEMS"], [table.name for table in result.tables])
        self.assertEqual(["ITEM_ID", "NOTE"], [column.name for column in result.columns])
        self.assertEqual("'NOLOGGING'", result.columns[1].default_value)


if __name__ == "__main__":
    unittest.main()
