"""spec 051 — 컬럼 논리명이 집계 단계에서 소실되지 않는다."""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from pipeline.steps.step8_post_processing.table_logical_names import build_column_updates


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
