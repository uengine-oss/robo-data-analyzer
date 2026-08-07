"""DDL 파일 사이 중복 테이블 정의의 조기 실패 계약.

실측 회귀(2026-08-07 rwis): 프로시저 번들이 ddl 에 섞여 같은 테이블이 두 파일에서
정의됐는데 step1 이 통과시키고, LLM 소비가 끝난 step8 에서야 "duplicate IDs"로
죽었다. 모호성은 발생 지점(step1)에서 좌표(파일명들)와 함께 실패해야 한다.
"""
from __future__ import annotations

import unittest

from pipeline.steps.step1_parse_ddl.parse_ddl_step import _ensure_unique_table_ids


class DuplicateTableGuardTest(unittest.TestCase):
    def test_duplicate_across_files_fails_with_both_file_names(self) -> None:
        with self.assertRaises(ValueError) as caught:
            _ensure_unique_table_ids({
                "db:S.ORDERS": ["schema.sql", "bundle.sql"],
                "db:S.ITEMS": ["schema.sql"],
            })
        message = str(caught.exception)
        self.assertIn("db:S.ORDERS", message)
        self.assertIn("schema.sql", message)
        self.assertIn("bundle.sql", message)
        self.assertNotIn("db:S.ITEMS", message)

    def test_unique_tables_pass(self) -> None:
        _ensure_unique_table_ids({
            "db:S.ORDERS": ["schema.sql"],
            "db:S.ITEMS": ["schema.sql"],
        })


if __name__ == "__main__":
    unittest.main()
