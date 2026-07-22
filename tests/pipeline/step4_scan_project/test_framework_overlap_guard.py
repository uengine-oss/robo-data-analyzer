"""FR-003 가드 테스트 (spec 026) — 드롭목록 ∩ 프로젝트 code_nodes 표면화.

pytest 로도, ``python test_framework_overlap_guard.py`` 로도 실행 가능.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from pipeline.steps.step4_scan_project import scan_codebase_step as S
import unittest


class _Node:
    def __init__(self, _id: str, name: str) -> None:
        self.id = _id
        self.name = name


_CODE_NODES = [
    _Node("code:zapamcom10060.c:b000_main_proc", "b000_main_proc"),
    {"id": "opaque-id-that-must-not-be-parsed", "name": "a000_input_validation"},
]


def test_removes_project_functions_from_drop_list():
    bad = {"framework_runtime_functions": ["b000_main_proc", "a000_input_validation", "PRINT_*"]}
    with patch.object(S, "log_warning") as w:
        S._remove_project_functions_from_framework_drop(bad, _CODE_NODES)
        assert w.called
        assert w.call_args[0][1] == "framework_drop_project_functions_removed"
        assert bad["framework_runtime_functions"] == ["PRINT_*"]


def test_silent_when_only_runtime_functions():
    good = {"framework_runtime_functions": ["PFM_TRY", "mpfm_isnull", "PRINT_*"]}
    with patch.object(S, "log_warning") as w:
        S._remove_project_functions_from_framework_drop(good, _CODE_NODES)
        assert not w.called


def test_silent_when_empty_drop_list():
    with patch.object(S, "log_warning") as w:
        S._remove_project_functions_from_framework_drop({"framework_runtime_functions": []}, _CODE_NODES)
        assert not w.called


if __name__ == "__main__":
    test_removes_project_functions_from_drop_list()
    test_silent_when_only_runtime_functions()
    test_silent_when_empty_drop_list()
    print("[OK] FR-003 guard: 3/3 passed")


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
