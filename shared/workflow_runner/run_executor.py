"""Run 1회 실행 어댑터 — rmtree latest + LangGraph workflow 실행 + 종료 처리.

흐름:
1. ``logs/analysis.run/latest/`` + ``logs/llm_prompts/latest/`` rmtree + 재생성.
2. ``Run`` 메타 생성 + Neo4j 인덱스 / 제약 보장.
3. ``CostBudget`` + ``MemorySaver`` (Checkpointer) 생성.
4. Strategy 의 workflow 빌더 호출 → ``astream(stream_mode="updates")``.
5. 산출물 4파일 (artifacts/) + ``usage_summary.json``.

**일시정지 / 재개 정책 (Plan §4 M1)** — LangGraph Checkpointer 가 흡수:
- 별도 컨트롤러 / control endpoint 없음.
- 클라이언트가 NDJSON stream 끊으면 자동 일시정지 (MemorySaver 가 state 보존).
- 같은 ``thread_id`` 로 ``/analyze`` 재호출 시 자동 resume.
- stop = 클라이언트가 stream 끊고 다시 안 부르면 끝 (state 는 메모리에서 GC).
"""
from __future__ import annotations

import json
import shutil
from collections import defaultdict
from pathlib import Path
from typing import Any, Optional

from langgraph.checkpoint.memory import MemorySaver

from data_models.project_and_run import Project, Run
from shared.config.env_catalog import ENV
import time

from shared.llm.cost_budget import CostBudget
from shared.logging_and_stream.logger import (
    attach_run_file_handlers,
    detach_run_file_handlers,
    log_error,
    log_info,
)
from shared.logging_and_stream.ndjson_stream import NDJSONStream
from shared.logging_and_stream.prompt_logger import reset_counters
from shared.neo4j.neo4j_client import Neo4jClient
from shared.neo4j.schema_constraints import ensure_schema
from shared.workflow_runner.artifact_dumper import dump_state_artifacts
from shared.workflow_runner.langgraph_workflow import build_langgraph_workflow


async def execute_run(
    strategy: Any,
    project: Project,
    stream: Optional[NDJSONStream] = None,
    thread_id: Optional[str] = None,
) -> Run:
    """Run 1회 실행.

    Args:
        strategy: ``FRAMEWORK_PIPELINE`` 또는 ``DBMS_PIPELINE`` (``analysis_pipelines``).
        project: 분석 대상.
        stream: 프론트 NDJSON 스트림.
        thread_id: LangGraph Checkpointer thread 식별자 (None=run_id).
            같은 thread_id 로 재호출 시 마지막 state 복원 (일시정지/재개).
    """
    log_base = ENV.log.dir
    _reset_latest_dirs(log_base)
    reset_counters()

    run = Run.now(project=project, log_base=log_base)
    run.run_dir.mkdir(parents=True, exist_ok=True)
    (run.run_dir / "artifacts").mkdir(parents=True, exist_ok=True)

    if thread_id is None:
        thread_id = run.run_id

    # ── 파일 핸들러 attach (ROBO_LOG_FILE_ENABLED=true 일 때만) ──
    # analyzer.log (INFO+) + error.log (WARNING+) → tail -f 로 실시간 추적 가능.
    # 운영기 OFF → 빈 리스트 → detach no-op → 디스크 I/O 0.
    # mkdir 직후 attach — 즉시 발생하는 ensure_schema / LangGraph 빌드 / step 로그 다 포함.
    file_handlers = attach_run_file_handlers(run.run_dir)
    run_started_at = time.monotonic()
    log_info(
        "run",
        "start",
        project=project.name,
        strategy=getattr(strategy, "name", "?"),
        run_id=run.run_id,
        thread_id=thread_id,
    )
    try:
        neo_client = Neo4jClient()
        try:
            await ensure_schema(neo_client)
            log_info("run", "neo4j_ready", database=ENV.neo4j.database)

            cost_budget = CostBudget()
            checkpointer = MemorySaver()
            app = build_langgraph_workflow(strategy, checkpointer=checkpointer)

            initial_state: dict = {"run": run}
            config = {
                "configurable": {
                    "thread_id": thread_id,
                    "strategy": strategy,
                    "project": project,
                    "run": run,
                    "cost_budget": cost_budget,
                    "stream": stream,
                    "neo_client": neo_client,
                }
            }

            if stream is not None:
                # emit 안에서 logger mirror 가 자동으로 analyzer.log 에 phase_event 한 줄.
                stream.emit({"type": "phase_event", "event": "start", "thread_id": thread_id})

            final_state: dict = {}
            async for chunk in app.astream(initial_state, config=config, stream_mode="updates"):
                if isinstance(chunk, dict):
                    for node_name, state_update in chunk.items():
                        if stream is not None:
                            stream.emit({"type": "phase_event", "event": "node_done", "node": node_name})
                        if isinstance(state_update, dict):
                            final_state.update(state_update)

            # artifacts/ 4파일 — 실패 시 명시 emit + 상위로 전파 (silent X)
            try:
                dump_state_artifacts(run, final_state)
                log_info("run", "artifacts_dumped", run_dir=str(run.run_dir))
            except Exception as e:
                log_error(
                    "run",
                    "dump_artifacts_failed",
                    e,
                    stream_emit=stream.emit if stream is not None else None,
                    run_id=run.run_id,
                )
                raise
            _summarize_usage(run.run_dir)

            if stream is not None:
                stream.emit({"type": "phase_event", "event": "complete", "thread_id": thread_id})

            log_info(
                "run",
                "complete",
                run_id=run.run_id,
                elapsed_sec=f"{time.monotonic() - run_started_at:.1f}",
            )

        finally:
            await neo_client.close()
    finally:
        # neo close 다음 — Windows fd 충돌 회피 위해 attach 의 역순.
        detach_run_file_handlers(file_handlers)

    if stream is not None:
        await stream.close()
    return run


def _reset_latest_dirs(log_base: Path) -> None:
    """매 실행 ``latest/`` rmtree 후 재생성 (Plan §4 M1)."""
    for sub in ("analysis.run/latest", "llm_prompts/latest"):
        d = log_base / sub
        if d.exists():
            shutil.rmtree(d, ignore_errors=True)
        d.mkdir(parents=True, exist_ok=True)


def _summarize_usage(run_dir: Path) -> None:
    """``usage.jsonl`` → ``usage_summary.json`` 집계."""
    usage_path = run_dir / "usage.jsonl"
    if not usage_path.exists():
        return

    per_agent_model: dict[tuple[str, str], dict] = defaultdict(lambda: {
        "input_tokens": 0,
        "output_tokens": 0,
        "cached_tokens": 0,
        "total_tokens": 0,
        "cost_usd": 0.0,
        "calls": 0,
    })
    total = {
        "input_tokens": 0,
        "output_tokens": 0,
        "cached_tokens": 0,
        "total_tokens": 0,
        "cost_usd": 0.0,
        "calls": 0,
    }

    with usage_path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            key = (record.get("agent", "?"), record.get("model", "?"))
            bucket = per_agent_model[key]
            for field in ("input_tokens", "output_tokens", "cached_tokens", "total_tokens"):
                bucket[field] += int(record.get(field, 0))
                total[field] += int(record.get(field, 0))
            bucket["cost_usd"] += float(record.get("cost_usd", 0.0))
            total["cost_usd"] += float(record.get("cost_usd", 0.0))
            bucket["calls"] += 1
            total["calls"] += 1

    summary = {
        "total": total,
        "per_agent_model": [
            {"agent": agent, "model": model, **stats}
            for (agent, model), stats in sorted(per_agent_model.items())
        ],
    }
    (run_dir / "usage_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
