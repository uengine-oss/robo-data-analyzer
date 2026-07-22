"""spec 051 — 컬럼 논리명이 집계 단계에서 소실되지 않는다."""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from pipeline.steps.step8_post_processing.table_logical_names import build_column_updates
import unittest


def test_logical_name_without_description_is_preserved():
    updates = build_column_updates(
        {"db:shipping": [{}]},
        {"db:shipping": {}},
        {"db:shipping": {"ship_seq": "배송순번"}},
    )
    assert updates == [{
        "id": "db:shipping.ship_seq",
        "analyzed_description": "",
        "logical_name": "배송순번",
    }]


def test_description_and_logical_name_union_is_complete():
    updates = build_column_updates(
        {"db:shipping": [{}]},
        {"db:shipping": {"ship_seq": "배송 순번", "request_dtm": "요청 시각"}},
        {"db:shipping": {"ship_seq": "배송순번", "ship_status_cd": "배송상태"}},
    )
    assert {item["id"] for item in updates} == {
        "db:shipping.request_dtm", "db:shipping.ship_seq", "db:shipping.ship_status_cd",
    }


if __name__ == "__main__":
    test_logical_name_without_description_is_preserved()
    test_description_and_logical_name_union_is_complete()
    print("[OK] table_logical_names: 2/2 passed")


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
