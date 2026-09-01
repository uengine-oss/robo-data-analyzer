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
    python scripts/run_analysis.py

    # 1 프로젝트만 (sub 디렉토리 이름)
    python scripts/run_analysis.py apascrt00010t02_20260406

    # strategy 강제 override
    python scripts/run_analysis.py apascrt00010t02_20260406 --strategy framework

    # 코퍼스 루트를 명령에서 직접 지정 (.env 수정 없이 임의 코퍼스 실행)
    python scripts/run_analysis.py --data-dir D:\\corpora\\my_project --strategy framework

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

from pipeline.pipeline_strategies import get_pipeline
from pipeline.source_languages import detect_strategy
from pipeline.models.analysis_meta import AnalysisRun
from pipeline.models.analysis_meta import Codebase
from shared.config.app_settings import SETTINGS
from shared.events.ndjson_event_stream import NDJSONStream
from shared.events.runtime_logger import redact_sensitive_text
from pipeline.execution.run_executor import execute_run
from pipeline.execution.analysis_profile import (
    AnalysisProfile,
    ResolvedAnalysisInputs,
    load_analysis_profile,
    preflight_lines,
    resolve_analysis_inputs,
)
from graph.connection_context import Neo4jOverride, set_override


class AnalysisBatchError(RuntimeError):
    """하나 이상의 프로젝트 분석이 실패한 운영자 CLI 종료 상태."""

    def __init__(self, failures: list[tuple[Path, Exception]]) -> None:
        self.failures = failures
        names = ", ".join(path.name for path, _ in failures)
        super().__init__(f"analysis failed for {len(failures)} project(s): {names}")


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
        elif etype == "error":
            print(f"[ERROR] {event.get('component','-')} : {str(event.get('message',''))[:200]}")
        elif etype == "warning":
            print(f"[WARN ] {event.get('component','-')} : {str(event.get('message',''))[:150]}")
        elif etype == "stream_end":
            print("[END  ] stream closed")
            break
        else:
            print(f"[{etype.upper()[:5]}] {text[:200]}")


async def analyze_one(
    project_dir: Path,
    inputs: ResolvedAnalysisInputs,
) -> AnalysisRun:
    """1 프로젝트 분석 + 결과 요약 출력."""
    print(f"\n{'='*60}")
    print(f"  project: {project_dir.name}")
    print(f"  root:    {project_dir}")
    print(f"  ddl:     {inputs.ddl_dir or '(없음)'}")
    print(f"  strategy: {inputs.strategy}")
    print(f"{'='*60}\n")

    pipeline = get_pipeline(inputs.strategy)
    project = Codebase(
        root=project_dir,
        strategy=inputs.strategy,  # type: ignore[arg-type]
        ddl_dir=inputs.ddl_dir,
        locale=inputs.locale,
        # CLI/테스트 전용: 어느 DB를 샘플링할지 환경변수로 받음(운영 HTTP는 AnalyzeRequest 입력).
        # 비어 있으면 None → 샘플 보강 skip.
        datasource=SETTINGS.catalog_api.test_datasource or None,
        corpus_root=inputs.data_dir,
        selected_source_ids=inputs.selected_source_ids,
        validation_baseline_ref=inputs.validation_baseline_ref,
        validation_baseline_sha256=inputs.validation_baseline_sha256,
        validation_answer_key_ref=inputs.validation_answer_key_ref,
        validation_answer_key_sha256=inputs.validation_answer_key_sha256,
    )

    stream = NDJSONStream()
    run_task = asyncio.create_task(
        execute_run(
            pipeline,
            project,
            stream=stream,
            run_output_root=inputs.run_output,
        )
    )
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


def _select_data_dir(
    profile: AnalysisProfile | None,
    explicit_data_dir: Path | None,
) -> Path:
    return (
        explicit_data_dir
        or (profile.data_dir if profile else None)
        or SETTINGS.analysis_data.dir
    ).expanduser().resolve()


async def main(
    target: str | None,
    strategy_override: str | None,
    *,
    profile_path: Path | None = None,
    data_dir_override: Path | None = None,
    database_override: str | None = None,
    locale_override: str | None = None,
    run_output_override: Path | None = None,
) -> None:
    profile = load_analysis_profile(profile_path) if profile_path else None
    data_dir = _select_data_dir(profile, data_dir_override)
    source_dir = data_dir / "source"

    if not source_dir.is_dir():
        raise ValueError(f"source directory not found: {source_dir}")

    # target 명시 → 1 프로젝트 / 미명시 → 모든 sub
    if target:
        project_dir = source_dir / target
        if not project_dir.is_dir():
            available = [p.name for p in source_dir.iterdir() if p.is_dir()]
            raise ValueError(f"'{target}' 디렉토리 없음. 사용 가능: {available}")
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
            raise ValueError(f"source/ 가 비어 있음: {source_dir}")

    failures: list[tuple[Path, Exception]] = []
    for pd in targets:
        detected_strategy = (
            None
            if strategy_override or profile is not None
            else detect_strategy(pd)
        )
        inputs = resolve_analysis_inputs(
            profile=profile,
            explicit_data_dir=data_dir_override,
            explicit_strategy=strategy_override,
            explicit_database=database_override,
            explicit_locale=locale_override,
            explicit_run_output=run_output_override,
            default_data_dir=SETTINGS.analysis_data.dir,
            default_database=SETTINGS.graph_database.database,
            default_locale="ko",
            default_run_output=SETTINGS.run_output.dir,
            detected_strategy=detected_strategy,
        )
        print("\n=== Analysis preflight ===")
        for line in preflight_lines(inputs):
            print(f"  {line}")

        # 기존 연결 설정(URI/user/password)은 유지하고 database만 run 입력으로 명시한다.
        # contextvar라 이 분석 task에만 적용되며 SETTINGS/.env를 수정하지 않는다.
        set_override(Neo4jOverride(
            uri=SETTINGS.graph_database.uri,
            user=SETTINGS.graph_database.user,
            password=SETTINGS.graph_database.password,
            database=inputs.neo4j_database,
        ))
        try:
            await analyze_one(pd, inputs)
        except Exception as e:
            safe_error = redact_sensitive_text(f"{type(e).__name__}: {e}")
            print(f"\n[FAIL] {pd.name}: {safe_error}")
            failures.append((pd, e))

    if failures:
        raise AnalysisBatchError(failures)


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
    parser.add_argument(
        "--data-dir",
        help="코퍼스 루트 직접 지정 (source/ ddl/ analysis/ 구조). "
             "ROBO_DATA_DIR env 보다 우선 — .env 수정 없이 임의 코퍼스 실행.",
    )
    parser.add_argument(
        "--profile", type=Path,
        help="분석 실행 프로필 YAML. 명시 CLI 인자가 프로필보다 우선.",
    )
    parser.add_argument(
        "--database",
        help="Neo4j database 명시 override (프로필/.env보다 우선, system 금지).",
    )
    parser.add_argument(
        "--locale",
        help="출력 언어 태그 (예: ko, en-US). 프로필/기본값보다 우선.",
    )
    parser.add_argument(
        "--run-output", type=Path,
        help="이번 실행의 출력 루트. 생략하면 기존 ./logs, 명시하면 해당 경로를 사용.",
    )
    args = parser.parse_args()
    try:
        asyncio.run(main(
            args.target,
            args.strategy,
            profile_path=args.profile,
            data_dir_override=Path(args.data_dir) if args.data_dir else None,
            database_override=args.database,
            locale_override=args.locale,
            run_output_override=args.run_output,
        ))
    except AnalysisBatchError as error:
        print(f"\n[FAIL] {error}", file=sys.stderr)
        raise SystemExit(1) from error
