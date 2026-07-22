"""Framework/DBMS 공통 테이블 프롬프트 JSON 계약 테스트."""
from __future__ import annotations

import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from pipeline.steps.step7_analyze_code.table_prompt_context import dump_table_refs
import unittest


def test_table_id_is_separate_from_description_and_columns() -> None:
    dumped = dump_table_refs(
        ("db:comm_code",),
        {
            "db:comm_code": {
                "description": "공통코드: 설명에 콜론이 있어도 ID가 아니다",
                "columns": ["code_group", "code_value"],
                "sample_rows": [],
            },
        },
    )
    records = json.loads(dumped)
    assert records == [{
        "table_id": "db:comm_code",
        "description": "공통코드: 설명에 콜론이 있어도 ID가 아니다",
        "columns": ["code_group", "code_value"],
        "sample_rows": [],
    }]
    assert not dumped.startswith("db:comm_code:")


def test_empty_and_multiple_table_sets_are_exact() -> None:
    contexts = {
        "db:orders": {"description": "주문", "columns": ["order_id"]},
        "db:shipping": {"description": "배송", "columns": ["order_id"]},
    }
    assert dump_table_refs((), contexts) == "[]"
    records = json.loads(dump_table_refs(("db:orders", "db:shipping"), contexts))
    assert [record["table_id"] for record in records] == ["db:orders", "db:shipping"]


def test_framework_and_dbms_use_the_same_serializer() -> None:
    framework = (_ROOT / "pipeline/steps/step7_analyze_code/three_split_llm_fanout.py").read_text(encoding="utf-8")
    # DBMS 프롬프트 조립은 batch_prompt_values 로 이사 (2026-07 리팩토링) — 계약 대상 갱신.
    dbms = (_ROOT / "pipeline/steps/step7_analyze_code/batch_prompt_values.py").read_text(encoding="utf-8")
    assert '"reads": dump_table_refs(ctx.reads, table_descriptions)' in framework
    assert '"writes": dump_table_refs(ctx.writes, table_descriptions)' in framework
    assert "reads(JSON): {dump_table_refs(ctx.reads, table_descriptions)}" in dbms
    assert "writes(JSON): {dump_table_refs(ctx.writes, table_descriptions)}" in dbms


if __name__ == "__main__":
    test_table_id_is_separate_from_description_and_columns()
    test_empty_and_multiple_table_sets_are_exact()
    test_framework_and_dbms_use_the_same_serializer()
    print("[OK] table prompt context: 3/3 passed")


# ── 스위트 편입 브리지 (2026-07-22 테스트 정리) ──────────────────────────
# 함수식 케이스는 unittest discover 에 수집되지 않아 스위트에서 실행되지 않는
# 일회성이었다. 아래 브리지가 전 케이스를 스위트 회귀로 편입한다 (재사용 보장).
class ModuleCasesSuiteBridge(unittest.TestCase):
    def test_module_cases(self):
        import asyncio
        import inspect
        cases = [(n, f) for n, f in sorted(globals().items())
                 if n.startswith("test_") and inspect.isfunction(f)]
        self.assertTrue(cases, "함수식 케이스가 없다")
        for name, fn in cases:
            with self.subTest(case=name):
                if inspect.iscoroutinefunction(fn):
                    asyncio.run(fn())
                else:
                    fn()
