"""spec 027 — constrain_tables_to_rw 테스트.

``python test_rw_table_filter.py`` 또는 pytest 로 실행.
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from analysis_flow.step6_analyze_code import rw_table_filter as M


def _insights(*tables):
    return NS(table_insights=[NS(table=t) for t in tables])


def _rules(*tables):
    return NS(examples=[NS(affected_tables=[NS(table=t) for t in tables])])


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
    assert [a.table for a in ru.examples[0].affected_tables] == ["zord_jncard"]


def test_empty_rw_drops_all():
    ins = _insights("anytable")
    ru = _rules("e800_dbio_x")
    dropped = M.constrain_tables_to_rw("dispatcher", [], [], ins, ru)
    assert dropped == 2
    assert ins.table_insights == []
    assert ru.examples[0].affected_tables == []


def test_keeps_all_when_in_rw():
    ins = _insights("t1")
    ru = _rules("t1", "t2")
    dropped = M.constrain_tables_to_rw("f3", ["t1"], ["t2"], ins, ru)
    assert dropped == 0


if __name__ == "__main__":
    test_drops_offrw_table_in_insights()
    test_drops_offrw_table_in_examples()
    test_empty_rw_drops_all()
    test_keeps_all_when_in_rw()
    print("[OK] rw_table_filter: 4/4 passed")
