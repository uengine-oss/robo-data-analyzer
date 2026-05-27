"""격리 시험 CLI — Qwen agent hang 원인 영역 좁히기.

사용::

    # 단일 시나리오 시도
    python -m experiments.qwen_agent_diagnose.diagnose --scenario C

    # 5 시나리오 일괄 시도 (A→B→D→E→C 순 — 정상 기대 우선, 위험한 C 마지막)
    python -m experiments.qwen_agent_diagnose.diagnose --all

    # timeout 조절 (기본 60초)
    python -m experiments.qwen_agent_diagnose.diagnose --scenario C --timeout 30

결과는 ``experiments/qwen_agent_diagnose/results.jsonl`` 에 누적. 한 줄 = 1 시도.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

# Windows cp949 콘솔에서 한글/특수문자 UnicodeEncodeError 방지.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from experiments.qwen_agent_diagnose.scenarios import (
    SCENARIOS,
    ScenarioResult,
    result_to_jsonl_dict,
)
from experiments.qwen_agent_diagnose.real_agent_scenarios import (
    REAL_SCENARIOS,
    RealScenarioResult,
    real_result_to_jsonl_dict,
)


_RESULTS_PATH = Path(__file__).resolve().parent / "results.jsonl"


def _print_result(result: Any) -> None:
    """결과 한 줄 출력 (mock A~E + 실제 R1~R7 공통)."""
    status_icon = {
        "success": "OK",
        "timeout": "TIMEOUT",
        "error": "ERROR",
        "empty_response": "EMPTY",
    }.get(result.status, "?")
    is_real = hasattr(result, "invoke_mode")
    if is_real:
        opts = (
            f"tools={'9 ' if result.use_tools else '0 '} "
            f"format={'ON ' if result.use_response_format else 'OFF'} "
            f"mode={result.invoke_mode:<10} "
            f"input={result.input_kind}"
        )
    else:
        opts = (
            f"tool={'ON ' if result.tool_calling else 'OFF'} "
            f"format={'ON ' if result.response_format else 'OFF'}"
        )
    print(
        f"  [{result.scenario}] {status_icon:<7} {opts} "
        f"~{result.input_tokens_estimate}tok "
        f"elapsed={result.elapsed_sec:.2f}s "
        f"resp_len={result.response_length}"
    )
    if result.status == "error":
        print(f"      error: {result.error_type}: {result.error_message[:200]}")
    elif result.status == "timeout":
        print(f"      hang 확정 — {result.error_message}")
    elif result.output_preview:
        print(f"      preview: {result.output_preview[:200]!r}")


def _append_jsonl(result: Any) -> None:
    """결과를 results.jsonl 에 append (한 줄 = 1 시도)."""
    if hasattr(result, "invoke_mode"):
        record = real_result_to_jsonl_dict(result)
    else:
        record = result_to_jsonl_dict(result)
    record["timestamp"] = datetime.now().isoformat(timespec="seconds")
    _RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _RESULTS_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


async def _run_one(scenario: str, timeout: float) -> Any:
    """1 시나리오 시도 → 결과 출력 + jsonl 적재. mock(A~E) / 실제(R1~R7) 자동 분기."""
    if scenario in REAL_SCENARIOS:
        runner = REAL_SCENARIOS[scenario]
    elif scenario in SCENARIOS:
        runner = SCENARIOS[scenario]
    else:
        raise ValueError(f"Unknown scenario: {scenario} (use A~E or R1~R7)")
    print(f"\n→ 시나리오 {scenario} 시작 (timeout {timeout}s)...")
    result = await runner(timeout_sec=timeout)
    _print_result(result)
    _append_jsonl(result)
    return result


async def _run_all_mock(timeout: float) -> None:
    """mock 5 시나리오 (A~E) — LLM 자체·tool 자체 baseline 확인용."""
    print("=" * 70)
    print("mock 격리 시험 — A~E (LLM/tool 자체 baseline, timeout 권장 60s)")
    print("=" * 70)
    for scenario in ["A", "B", "D", "E", "C"]:
        await _run_one(scenario, timeout)


async def _run_all_real(timeout: float) -> None:
    """실제 본체 재현 7 시나리오 (R1~R7) — hang 정확 재현."""
    print("=" * 70)
    print("실제 본체 재현 시험 — R1~R7 (본체 코드 정확 재현, timeout 권장 300s=5분)")
    print("=" * 70)
    # 위험한 R1 (현재 hang) 을 가장 먼저 — 다른 시나리오가 정상이면 R1 만 hang 확정
    for scenario in ["R6", "R4", "R3", "R2", "R5", "R7", "R1"]:
        await _run_one(scenario, timeout)
    print()
    print(f"결과 누적: {_RESULTS_PATH.relative_to(_ROOT)}")


async def _run_all_full(timeout_mock: float, timeout_real: float) -> None:
    """mock + 실제 전체 시리즈."""
    await _run_all_mock(timeout_mock)
    print()
    await _run_all_real(timeout_real)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Qwen agent hang 격리 시험 — mock(A~E) + 실제 본체 재현(R1~R7).",
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--scenario", choices=["A", "B", "C", "D", "E", "R1", "R2", "R3", "R4", "R5", "R6", "R7"],
        help="시도할 시나리오 (1 개)",
    )
    group.add_argument("--all-mock", action="store_true", help="mock 5 (A~E)")
    group.add_argument("--all-real", action="store_true", help="실제 본체 재현 7 (R1~R7)")
    group.add_argument("--all", action="store_true", help="mock 5 + 실제 7 = 12 시나리오 일괄")
    parser.add_argument(
        "--timeout", type=float, default=300.0,
        help="응답 timeout 초 (기본 300=5분). hang 감지 = 이 시간 안에 응답 종료 X",
    )
    args = parser.parse_args()

    if args.all_mock:
        asyncio.run(_run_all_mock(min(args.timeout, 60.0)))
    elif args.all_real:
        asyncio.run(_run_all_real(args.timeout))
    elif args.all:
        asyncio.run(_run_all_full(60.0, args.timeout))
    else:
        asyncio.run(_run_one(args.scenario, args.timeout))


if __name__ == "__main__":
    main()
