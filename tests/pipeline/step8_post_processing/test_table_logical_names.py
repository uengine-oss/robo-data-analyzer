"""spec 051 — 컬럼 논리명이 집계 단계에서 소실되지 않는다."""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from types import SimpleNamespace as NS

from pipeline.steps.step8_post_processing.table_logical_names import (
    build_column_updates,
    build_consolidated_column_updates,
)
import unittest
from tests.unittest_function_cases import load_function_cases


def _assert_value_error(call) -> None:
    try:
        call()
    except ValueError:
        return
    raise AssertionError("expected ValueError")


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


def test_consolidated_result_requires_exact_column_set():
    result = NS(columns=[
        NS(name="status_cd", logical_name="처리상태", description="처리 상태 코드"),
        NS(name="missing_col", logical_name="누락컬럼", description="누락 컬럼 설명"),
    ])
    updates = build_consolidated_column_updates(
        ["db:orders"],
        [result],
        {"db:orders": {"status_cd", "missing_col"}},
    )
    assert {item["id"] for item in updates} == {
        "db:orders.status_cd", "db:orders.missing_col",
    }

    blank = build_consolidated_column_updates(
        ["db:unknown"],
        [NS(columns=[NS(name="opaque", logical_name="", description="")])],
        {"db:unknown": {"opaque"}},
    )
    assert blank == [{
        "id": "db:unknown.opaque", "logical_name": "", "analyzed_description": "",
    }]


def test_consolidated_result_rejects_unknown_duplicate_missing_and_empty_values():
    result = NS(columns=[
        NS(name="status_cd", logical_name="처리상태", description="처리 상태 코드"),
        NS(name="status_cd", logical_name="다른상태", description="중복 후보"),
        NS(name="sibling_blob", logical_name="응답원문", description="다른 테이블 컬럼"),
    ])
    _assert_value_error(lambda: build_consolidated_column_updates(
            ["db:orders"],
            [result],
            {"db:orders": {"status_cd", "missing_col"}},
        ))

    for invalid_results in (
        [],
        [None],
    ):
        _assert_value_error(lambda invalid_results=invalid_results:
                build_consolidated_column_updates(
                    ["db:orders"],
                    invalid_results,
                    {"db:orders": {"status_cd"}},
                ))


if __name__ == "__main__":
    test_logical_name_without_description_is_preserved()
    test_description_and_logical_name_union_is_complete()
    print("[OK] table_logical_names: 2/2 passed")


def load_tests(_loader, standard_tests, _pattern):
    return load_function_cases(standard_tests, globals())
