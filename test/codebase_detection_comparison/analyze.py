"""CLI 진입점.

사용법:
    python -m test.codebase_detection_comparison.analyze <path>

프로그래밍:
    result = await analyze("/some/project")
    print(result["summary"])
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .agent import count_tool_calls, create_agent

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 300.0


async def analyze(
    path: str,
    api_key: str = "",
    timeout: float = DEFAULT_TIMEOUT,
    use_cache: bool = True,
) -> dict[str, Any]:
    """코드베이스 분석 — 항상 Deep Agent 경유. use_cache=False 면 순수 로직."""
    root = Path(path).resolve()
    if not root.exists():
        return {"summary": "", "error": f"path not found: {root}"}

    agent, holder = create_agent(root, api_key=api_key, use_cache=use_cache)
    user_msg = (
        "이 프로젝트를 분석해. quick_collect 를 먼저 호출하고, "
        "캐시 히트면 그 summary 를 그대로 submit_report 로 전달해 종료. "
        "캐시 미스면 결과를 보고 명확하면 즉시 submit_report, 모호하면 file-tracker 1 회 후 submit. "
        "총 도구 호출 5 회 이내."
    )

    started = time.time()
    try:
        result = await asyncio.wait_for(
            asyncio.get_event_loop().run_in_executor(
                None,
                lambda: agent.invoke({"messages": [{"role": "user", "content": user_msg}]}),
            ),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        elapsed = time.time() - started
        return {
            "summary": holder.get("summary") or "",
            "info": asdict(holder["info"]) if holder.get("info") else None,
            "error": "timeout",
            "elapsed_sec": elapsed,
        }

    elapsed = time.time() - started
    return {
        "summary": holder.get("summary") or "(submit_report 미호출)",
        "info": asdict(holder["info"]) if holder.get("info") else None,
        "cache_hit": holder.get("cache_hit", False),
        "cache_key": holder.get("cache_key"),
        "tool_calls_count": count_tool_calls(result),
        "elapsed_sec": elapsed,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="코드베이스 프레임워크/DB 접근 패턴 분석 (Deep Agent)")
    parser.add_argument("path", nargs="?", default=".", help="분석할 프로젝트 루트 경로")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT, help="전체 타임아웃(초)")
    parser.add_argument("--no-cache", action="store_true", help="캐시 조회/저장 건너뛰기 (순수 로직 검증)")
    parser.add_argument("--verbose", "-v", action="store_true", help="디버그 로그")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    out = asyncio.run(analyze(args.path, timeout=args.timeout, use_cache=not args.no_cache))

    print("\n=== 분석 결과 ===\n")
    print(out.get("summary") or "(empty)")
    print("\n=== 메타 ===")
    print(f"cache_hit      : {out.get('cache_hit')}")
    print(f"cache_key      : {out.get('cache_key')}")
    print(f"tool_calls     : {out.get('tool_calls_count')}")
    print(f"elapsed        : {out.get('elapsed_sec', 0):.1f}s")
    if out.get("error"):
        print(f"error          : {out['error']}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
