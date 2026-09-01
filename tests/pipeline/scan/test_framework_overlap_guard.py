"""FR-003 가드 테스트 (spec 026) — 드롭목록 ∩ 프로젝트 code_nodes 표면화.

pytest 로도, ``python test_framework_overlap_guard.py`` 로도 실행 가능.
"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from agents.scan_agent import validate_framework_runtime_contract
import unittest
from tests.unittest_function_cases import load_function_cases


class _Node:
    def __init__(self, _id: str, name: str, label: str = "FUNCTION") -> None:
        self.id = _id
        self.name = name
        self.label = label


_CODE_NODES = [
    _Node("code:zapamcom10060.c:b000_main_proc", "b000_main_proc"),
    {"id": "opaque-id-that-must-not-be-parsed", "name": "a000_input_validation",
     "label": "FUNCTION"},
    # AST statement 노드 — goto 라벨 이름은 루틴 정의가 아니다(framework 실측:
    # `goto PFM_CATCH;` 의 GOTO 노드가 skill 런타임 함수와 거짓 충돌해 run fatal).
    _Node("code:zapamcom10100.c:goto@305", "PFM_CATCH", label="GOTO"),
]


def test_exact_project_function_conflict_is_rejected():
    try:
        validate_framework_runtime_contract(
            ["b000_main_proc", "PFM_TRY"], _CODE_NODES,
        )
    except ValueError as error:
        assert "b000_main_proc" in str(error)
    else:
        raise AssertionError("project/runtime exact conflict was accepted")


def test_glob_project_function_conflict_is_rejected():
    try:
        validate_framework_runtime_contract(["a000_*"], _CODE_NODES)
    except ValueError as error:
        assert "a000_input_validation" in str(error)
    else:
        raise AssertionError("project/runtime glob conflict was accepted")


def test_runtime_only_and_empty_contracts_pass_unchanged():
    runtime = ["PFM_TRY", "mpfm_isnull", "PRINT_*"]
    assert validate_framework_runtime_contract(runtime, _CODE_NODES) is None
    assert validate_framework_runtime_contract([], _CODE_NODES) is None


def test_statement_node_label_name_is_not_a_routine_conflict():
    # PFM_CATCH 는 GOTO 라벨로만 존재 — 런타임 함수 선언과 충돌이 아니다.
    assert validate_framework_runtime_contract(["PFM_CATCH"], _CODE_NODES) is None


if __name__ == "__main__":
    test_exact_project_function_conflict_is_rejected()
    test_glob_project_function_conflict_is_rejected()
    test_runtime_only_and_empty_contracts_pass_unchanged()
    test_statement_node_label_name_is_not_a_routine_conflict()
    print("[OK] FR-003 guard: 4/4 passed")


def load_tests(_loader, standard_tests, _pattern):
    return load_function_cases(standard_tests, globals())
