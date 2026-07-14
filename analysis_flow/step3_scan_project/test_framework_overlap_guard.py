"""FR-003 가드 테스트 (spec 026) — 드롭목록 ∩ 프로젝트 code_nodes 표면화.

pytest 로도, ``python test_framework_overlap_guard.py`` 로도 실행 가능.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from analysis_flow.step3_scan_project import scan_codebase_step as S


class _Node:
    def __init__(self, _id: str, name: str) -> None:
        self.id = _id
        self.name = name


_CODE_NODES = [
    _Node("code:zapamcom10060.c:b000_main_proc", "b000_main_proc"),
    {"id": "opaque-id-that-must-not-be-parsed", "name": "a000_input_validation"},
]


def test_warns_when_project_functions_in_drop_list():
    bad = {"framework_runtime_functions": ["b000_main_proc", "a000_input_validation", "PRINT_*"]}
    with patch.object(S, "log_warning") as w:
        S._warn_if_framework_overlaps_project(bad, _CODE_NODES)
        assert w.called
        assert w.call_args[0][1] == "framework_drop_overlaps_project"


def test_silent_when_only_runtime_functions():
    good = {"framework_runtime_functions": ["PFM_TRY", "mpfm_isnull", "PRINT_*"]}
    with patch.object(S, "log_warning") as w:
        S._warn_if_framework_overlaps_project(good, _CODE_NODES)
        assert not w.called


def test_silent_when_empty_drop_list():
    with patch.object(S, "log_warning") as w:
        S._warn_if_framework_overlaps_project({"framework_runtime_functions": []}, _CODE_NODES)
        assert not w.called


if __name__ == "__main__":
    test_warns_when_project_functions_in_drop_list()
    test_silent_when_only_runtime_functions()
    test_silent_when_empty_drop_list()
    print("[OK] FR-003 guard: 3/3 passed")
