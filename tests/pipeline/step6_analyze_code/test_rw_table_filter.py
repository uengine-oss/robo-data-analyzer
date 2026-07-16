"""spec 027 — constrain_tables_to_rw 테스트.

``python test_rw_table_filter.py`` 또는 pytest 로 실행.
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch

_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from pipeline.steps.step6_analyze_code import rw_table_filter as M


def _insights(*tables):
    return NS(table_insights=[NS(table=t) for t in tables])


def _insight_with_columns(table, *columns):
    return NS(table_insights=[NS(
        table=table,
        columns=[NS(name=name, logical_name=f"논리-{name}", description="") for name in columns],
    )])


def _rules(*tables):
    return NS(rules=[NS(examples=[NS(affected_tables=[NS(table=t) for t in tables])])])


def test_drops_offrw_table_in_insights():
    ins = _insights("zpay_ap_rltm_auth_hst", "zapamcom.zapamcom10140.e800_dbio_rltm_auth_hst_u0004")
    with patch.object(M, "log_warning") as w:
        dropped = M.constrain_tables_to_rw("f1", ["zpay_ap_rltm_auth_hst"], [], ins, None)
    assert dropped == 1
    assert [t.table for t in ins.table_insights] == ["zpay_ap_rltm_auth_hst"]
    assert w.called


def test_drops_offrw_table_in_examples():
    ru = _rules("zord_jncard", "zapamcom.zapamcom10140.d100_call_zapamcom10060_proc")
    dropped = M.constrain_tables_to_rw("f2", [], ["zord_jncard"], None, ru)
    assert dropped == 1
    assert [a.table for a in ru.rules[0].examples[0].affected_tables] == ["zord_jncard"]


def test_empty_rw_drops_all():
    ins = _insights("anytable")
    ru = _rules("e800_dbio_x")
    dropped = M.constrain_tables_to_rw("dispatcher", [], [], ins, ru)
    assert dropped == 2
    assert ins.table_insights == []
    assert ru.rules[0].examples[0].affected_tables == []


def test_keeps_all_when_in_rw():
    ins = _insights("t1")
    ru = _rules("t1", "t2")
    dropped = M.constrain_tables_to_rw("f3", ["t1"], ["t2"], ins, ru)
    assert dropped == 0


def test_rejects_nonexact_table_identifier_variants():
    ins = _insights("db:shipping", "db.shipping", "db:shipping:shipping", "shipping")
    ru = _rules("db.shipping", "db:shipping")
    dropped = M.constrain_tables_to_rw("f5", ["db:shipping"], [], ins, ru)
    assert dropped == 4
    assert [t.table for t in ins.table_insights] == ["db:shipping"]
    assert [at.table for at in ru.rules[0].examples[0].affected_tables] == ["db:shipping"]


def test_ambiguous_bare_name_is_not_attributed():
    ins = _insights("orders")
    dropped = M.constrain_tables_to_rw(
        "f6", ["db:shop.orders", "db:archive.orders"], [], ins, None,
    )
    assert dropped == 1
    assert ins.table_insights == []

    exact = _insights("db:shop.orders")
    assert M.constrain_tables_to_rw(
        "f6", ["db:shop.orders", "db:archive.orders"], [], exact, None,
    ) == 0
    assert exact.table_insights[0].table == "db:shop.orders"


def test_columns_are_limited_to_exact_ddl_allowlist():
    ins = _insight_with_columns("db:shipping", "SHIP_SEQ", "ship_seq", "GHOST_COLUMN")
    with patch.object(M, "log_warning") as warning:
        dropped = M.constrain_tables_to_rw(
            "f7", ["db:shipping"], [], ins, None,
            allowed_columns_by_table={"db:shipping": ["SHIP_SEQ", "SHIP_STATUS_CD"]},
        )
    assert dropped == 0
    assert ins.table_insights[0].table == "db:shipping"
    assert [column.name for column in ins.table_insights[0].columns] == ["SHIP_SEQ"]
    assert any(call.args[1] == "column_off_ddl" for call in warning.call_args_list)


def test_multiple_rw_tables_are_preserved_and_missing_table_is_reported():
    complete = _insights("db:orders", "db:shipping")
    assert M.constrain_tables_to_rw(
        "f8", ["db:orders"], ["db:shipping"], complete, None,
    ) == 0
    assert [item.table for item in complete.table_insights] == ["db:orders", "db:shipping"]

    incomplete = _insights("db:orders")
    with patch.object(M, "log_warning") as warning:
        M.constrain_tables_to_rw("f8", ["db:orders"], ["db:shipping"], incomplete, None)
    assert any(call.args[1] == "table_insight_missing" for call in warning.call_args_list)


def test_dispatcher_drop_logs_info_not_warning():
    # allowed 빔(디스패처) → 예상 드롭 → INFO (사고 로그 error.log 미오염), WARNING 0 (FR-005).
    ins = _insights("anytable")
    with patch.object(M, "log_info") as i, patch.object(M, "log_warning") as w:
        M.constrain_tables_to_rw("dispatcher", [], [], ins, None)
    assert i.called and not w.called


def test_offrw_misattribution_logs_warning_not_info():
    # allowed 있는데 밖 테이블 → 진짜 오귀속 → WARNING (error.log), INFO 0 (FR-005).
    ins = _insights("t1", "ghost_table")
    with patch.object(M, "log_info") as i, patch.object(M, "log_warning") as w:
        M.constrain_tables_to_rw("f4", ["t1"], [], ins, None)
    assert w.called and not i.called


def test_dispatcher_drops_accumulate_and_flush_once():
    # 디스패처 드롭은 run 집계에 누적, flush 가 error.log 1줄로 표면화 후 초기화 (FR-005a).
    M.reset_drop_stats()
    M.constrain_tables_to_rw("disp1", [], [], _insights("a", "b"), None)  # 2건
    M.constrain_tables_to_rw("disp2", [], [], _insights("c"), None)       # 1건
    assert M._drop_stats == {"funcs": 2, "count": 3}
    with patch.object(M, "log_warning") as w:
        M.flush_drop_summary()
    assert w.call_count == 1                                  # 집계 1줄만 (per-함수 N줄 아님)
    assert M._drop_stats == {"funcs": 0, "count": 0}          # flush 후 초기화


def test_flush_no_drops_is_silent():
    # 드롭 0인 run → 집계 줄도 없음 (SC-007).
    M.reset_drop_stats()
    with patch.object(M, "log_warning") as w:
        M.flush_drop_summary()
    assert not w.called


if __name__ == "__main__":
    test_drops_offrw_table_in_insights()
    test_drops_offrw_table_in_examples()
    test_empty_rw_drops_all()
    test_keeps_all_when_in_rw()
    test_rejects_nonexact_table_identifier_variants()
    test_ambiguous_bare_name_is_not_attributed()
    test_columns_are_limited_to_exact_ddl_allowlist()
    test_multiple_rw_tables_are_preserved_and_missing_table_is_reported()
    test_dispatcher_drop_logs_info_not_warning()
    test_offrw_misattribution_logs_warning_not_info()
    test_dispatcher_drops_accumulate_and_flush_once()
    test_flush_no_drops_is_silent()
    print("[OK] rw_table_filter: 12/12 passed")
