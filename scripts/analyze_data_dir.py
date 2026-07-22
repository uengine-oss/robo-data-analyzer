"""ROBO_DATA_DIR 기준 단발 / 일괄 분석 — 운영자 표준 진입점.

표준 디렉토리 규약 (``ROBO_DATA_DIR`` 환경변수, 미지정 시 robo-data-analyzer 형제 ``data``):

::

    ROBO_DATA_DIR/
        ddl/                  # 공통 DDL (모든 source 와 매핑)
            schema.sql
            ...
        source/
            project_a/        # 분석 대상 1
                *.c / *.java / *.py / ...
            project_b/
                ...

자동 결정:
- ``project_root`` = ``ROBO_DATA_DIR/source/<name>`` (하위 폴더). 하위 폴더 없이
  소스 파일이 ``source/`` 직속이면 ``source/`` 자체가 1 프로젝트 (폴더 존재를 가정 X).
- ``ddl_dir``      = ``ROBO_DATA_DIR/ddl`` (있으면)
- ``strategy``     = 소스 확장자로 자동 (.sql/.pkb 만 = dbms, 그 외 = framework)

사용::

    # source/ 안 모든 프로젝트 일괄
    python scripts/analyze_data_dir.py

    # 1 프로젝트만 (sub 디렉토리 이름)
    python scripts/analyze_data_dir.py apascrt00010t02_20260406

    # strategy 강제 override
    python scripts/analyze_data_dir.py apascrt00010t02_20260406 --strategy framework

NDJSON 스트림 실시간 출력 + 종료 후 ``usage_summary.json`` 요약.

★ Windows 콘솔 (cp949) 에서도 한글 print 깨지지 않게 stdout/stderr 를 UTF-8 로 재구성.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

# Windows cp949 콘솔에서 한글/특수문자 (— ★ 등) UnicodeEncodeError 방지.
# PYTHONIOENCODING 환경변수에 의존 X — 운영자 환경 무관하게 UTF-8 출력.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from pipeline.strategies import get_pipeline
from pipeline.languages import detect_strategy
from pipeline.models.analysis_meta import AnalysisRun
from pipeline.models.analysis_meta import Codebase
from shared.config.settings import SETTINGS
from shared.events.ndjson_event_stream import NDJSONStream
from pipeline.execution.run_executor import execute_run


async def _consume_stream(stream: NDJSONStream) -> None:
    """NDJSON 스트림 실시간 출력 — 진행 상태 즉시 표시."""
    async for line in stream.iter():
        text = line.decode("utf-8").strip()
        try:
            event = json.loads(text)
        except json.JSONDecodeError:
            print(f"[STREAM] {text[:200]}")
            continue
        etype = event.get("type", "?")
        if etype == "phase_event":
            print(f"[PHASE] {event.get('event','?')}  node={event.get('node','-')}")
        elif etype == "tool_event":
            print(f"[TOOL ] {event.get('event','?'):<10} {event.get('tool','-'):<25} {str(event.get('message',''))[:80]}")
        elif etype == "todo_update":
            todos = event.get("todos") or []
            in_progress = [
                (t.get("content") or "?") for t in todos
                if isinstance(t, dict) and t.get("status") == "in_progress"
            ]
            current = in_progress[0] if in_progress else "(none)"
            print(f"[TODO ] {len(todos)} items, in_progress: {current[:60]}")
        elif etype == "error":
            print(f"[ERROR] {event.get('component','-')} : {str(event.get('message',''))[:200]}")
        elif etype == "warning":
            print(f"[WARN ] {event.get('component','-')} : {str(event.get('message',''))[:150]}")
        elif etype == "stream_end":
            print("[END  ] stream closed")
            break
        else:
            print(f"[{etype.upper()[:5]}] {text[:200]}")


async def analyze_one(project_dir: Path, ddl_dir: Path | None, strategy: str) -> AnalysisRun:
    """1 프로젝트 분석 + 결과 요약 출력."""
    print(f"\n{'='*60}")
    print(f"  project: {project_dir.name}")
    print(f"  root:    {project_dir}")
    print(f"  ddl:     {ddl_dir or '(없음)'}")
    print(f"  strategy: {strategy}")
    print(f"{'='*60}\n")

    pipeline = get_pipeline(strategy)
    project = Codebase(
        root=project_dir,
        strategy=strategy,  # type: ignore[arg-type]
        ddl_dir=ddl_dir,
        locale="ko",
        # CLI/테스트 전용: 어느 DB를 샘플링할지 환경변수로 받음(운영 HTTP는 AnalyzeRequest 입력).
        # 비어 있으면 None → 샘플 보강 skip.
        datasource=SETTINGS.catalog_api.test_datasource or None,
    )

    stream = NDJSONStream()
    run_task = asyncio.create_task(execute_run(pipeline, project, stream=stream))
    consume_task = asyncio.create_task(_consume_stream(stream))

    try:
        run = await run_task
    finally:
        await consume_task

    # ── 결과 요약 ──
    print(f"\n=== {project_dir.name} — Result ===")
    print(f"  run_dir: {run.run_dir}")

    usage_path = run.run_dir / "usage_summary.json"
    if usage_path.exists():
        usage = json.loads(usage_path.read_text(encoding="utf-8"))
        total = usage.get("total", {})
        print(f"  calls={total.get('calls',0)}  "
              f"in={total.get('input_tokens',0):,}  "
              f"out={total.get('output_tokens',0):,}  "
              f"cached={total.get('cached_tokens',0):,}  "
              f"cost=${total.get('cost_usd',0):.6f}")

    return run


async def main(target: str | None, strategy_override: str | None) -> None:
    data_dir = SETTINGS.analysis_data.dir
    source_dir = data_dir / "source"
    ddl_dir = data_dir / "ddl"

    if not source_dir.is_dir():
        print(f"ERROR: source 디렉토리 없음: {source_dir}")
        print(f"  ROBO_DATA_DIR = {data_dir}")
        print("  ROBO_DATA_DIR/source/ 안에 분석 대상 프로젝트 디렉토리 두세요.")
        return

    ddl_path = ddl_dir if ddl_dir.is_dir() else None

    # target 명시 → 1 프로젝트 / 미명시 → 모든 sub
    if target:
        project_dir = source_dir / target
        if not project_dir.is_dir():
            available = [p.name for p in source_dir.iterdir() if p.is_dir()]
            print(f"ERROR: '{target}' 디렉토리 없음. 사용 가능: {available}")
            return
        targets = [project_dir]
    else:
        subdirs = sorted(p for p in source_dir.iterdir() if p.is_dir())
        if subdirs:
            targets = subdirs
            print(f"=== {len(targets)} 개 프로젝트 일괄 분석 ===")
        elif any(source_dir.iterdir()):
            # 하위 폴더 없이 소스 파일이 source/ 직속 — source/ 자체가 1 프로젝트.
            # source/<name>/ 구조를 항상 가정하지 않는다.
            targets = [source_dir]
        else:
            print(f"ERROR: source/ 가 비어 있음: {source_dir}")
            return

    for pd in targets:
        strategy = strategy_override or detect_strategy(pd)
        try:
            await analyze_one(pd, ddl_path, strategy)
        except Exception as e:
            print(f"\n[FAIL] {pd.name}: {type(e).__name__}: {e}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="ROBO_DATA_DIR 기준 단발/일괄 분석")
    parser.add_argument(
        "target", nargs="?",
        help="source/ 안 디렉토리 이름 (미명시 시 모두). 예: apascrt00010t02_20260406",
    )
    parser.add_argument(
        "--strategy", choices=["framework", "dbms"],
        help="strategy 강제 override (미명시 시 확장자 자동 감지).",
    )
    args = parser.parse_args()
    asyncio.run(main(args.target, args.strategy))
