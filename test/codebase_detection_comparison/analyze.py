"""CLI 진입점 — Deep Agent 로 프레임워크/DB 접근 패턴 분석.

사용법:
    python -m test.codebase_detection_comparison.analyze <path>
"""
from __future__ import annotations

import argparse
import asyncio
import io
import logging
import sys
import time
from pathlib import Path
from typing import Any

from .agent import count_tool_calls, create_agent, extract_final_text
from .collector import collect, render_analysis_context

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 600.0

MODULE_ROOT = Path(__file__).resolve().parent
PROMPT_PATH = MODULE_ROOT / "prompts" / "analysis_context.txt"


def _force_utf8_stdout() -> None:
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        buf = getattr(stream, "buffer", None)
        if buf is None:
            continue
        setattr(
            sys,
            stream_name,
            io.TextIOWrapper(buf, encoding="utf-8", errors="replace", line_buffering=True),
        )


async def analyze(
    path: str,
    api_key: str = "",
    timeout: float = DEFAULT_TIMEOUT,
) -> dict[str, Any]:
    root = Path(path).resolve()
    if not root.exists():
        return {"summary": "", "error": f"path not found: {root}"}

    info = collect(str(root))
    template = PROMPT_PATH.read_text(encoding="utf-8")
    user_message = render_analysis_context(info, template)

    agent = create_agent(root, api_key=api_key)

    started = time.time()
    try:
        result = await asyncio.wait_for(
            asyncio.get_event_loop().run_in_executor(
                None,
                lambda: agent.invoke({"messages": [{"role": "user", "content": user_message}]}),
            ),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        return {
            "summary": "",
            "elapsed_sec": time.time() - started,
            "error": "timeout",
        }

    return {
        "summary": extract_final_text(result) or "(요약 미생성)",
        "tool_calls_count": count_tool_calls(result),
        "elapsed_sec": time.time() - started,
    }


def main() -> int:
    _force_utf8_stdout()
    parser = argparse.ArgumentParser(description="코드베이스 프레임워크·DB 접근 패턴 분석")
    parser.add_argument("path", nargs="?", default=".", help="분석할 프로젝트 루트")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT, help="전체 타임아웃(초)")
    parser.add_argument("--verbose", "-v", action="store_true", help="디버그 로그")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    out = asyncio.run(analyze(args.path, timeout=args.timeout))

    print("\n=== 프레임워크·DB 요약 ===\n")
    print(out.get("summary") or "(empty)")
    print("\n=== 메타 ===")
    print(f"tool_calls  : {out.get('tool_calls_count')}")
    print(f"elapsed     : {out.get('elapsed_sec', 0):.1f}s")
    if out.get("error"):
        print(f"error       : {out['error']}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
