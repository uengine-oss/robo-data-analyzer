"""Run 1회 실행 어댑터 — rmtree latest + LangGraph workflow 실행 + 종료 처리.

흐름:
1. ``logs/analysis.run/latest/`` + ``logs/llm_prompts/latest/`` rmtree + 재생성.
2. ``Run`` 메타 생성 + Neo4j 인덱스 / 제약 보장.
3. ``CostBudget`` + ``MemorySaver`` (Checkpointer) + ``PauseController`` 생성.
4. Strategy 의 workflow 빌더 호출 → ``astream(stream_mode="updates")``.
5. 산출물 4파일 (artifacts/) + ``usage_summary.json``.

**run 정책** — 매 ``/analyze`` 호출 = 독립 run (``run_id`` 가 유일 식별자). 진행은 NDJSON stream.

**일시정지 / 재개 / 중단** (소프트 — 프로세스가 살아있는 동안의 hold):
- ``PauseController`` 를 만들어 ``run_registry`` 에 등록 → control 엔드포인트가 신호.
- astream 소비 루프가 step 경계마다 ``pause.gate()`` 로 소비(paused 면 hold).
- stop 은 ``RunStopped`` 로 정상 종료(실패 아님). 서버를 끄면 run 도 끝난다(디스크 재개 X).
- Checkpointer(MemorySaver)는 run 1회 안의 state 전파용 — wipe / thread_id 정책 그대로.
"""
from __future__ import annotations

import asyncio
import json
import shutil
from collections import defaultdict
from pathlib import Path
from typing import Any, Optional

from langgraph.checkpoint.memory import MemorySaver

from data_models.analysis_run import AnalysisRun
from data_models.codebase import Codebase
from shared.config.env_catalog import ENV
import time

from shared.llm.cost_budget import CostBudget
from shared.log.logger import (
    attach_run_file_handlers,
    detach_run_file_handlers,
    log_error,
    log_info,
)
from shared.log.ndjson_stream import NDJSONStream
from shared.neo4j.neo4j_client import Neo4jClient
from shared.neo4j.schema_constraints import ensure_schema, wipe_graph
from shared.workflow.artifact_writer import dump_state_artifacts
from shared.workflow.graph_builder import build_langgraph_workflow
from shared.workflow.pause_controller import PauseController
from shared.workflow.run_registry import clear as clear_active_run
from shared.workflow.run_registry import set_active as set_active_run


async def execute_run(
    strategy: Any,
    codebase: Codebase,
    stream: Optional[NDJSONStream] = None,
    database: Optional[str] = None,
) -> AnalysisRun:
    """AnalysisRun 1회 실행 — 매 호출이 독립 run (``run_id`` 가 유일 식별자).

    Args:
        strategy: ``FRAMEWORK_PIPELINE`` 또는 ``DBMS_PIPELINE`` (``analysis_pipelines``).
        codebase: 분석 대상.
        stream: 프론트 NDJSON 스트림.
    """
    log_base = ENV.log.dir
    _reset_logs_root(log_base)

    run = AnalysisRun.now(codebase=codebase, log_base=log_base)
    run.run_dir.mkdir(parents=True, exist_ok=True)
    (run.run_dir / "artifacts").mkdir(parents=True, exist_ok=True)

    # ── 파일 핸들러 attach (ROBO_LOG_FILE_ENABLED=true 일 때만) ──
    # analyzer.log (INFO+) + error.log (WARNING+) → tail -f 로 실시간 추적 가능.
    # 운영기 OFF → 빈 리스트 → detach no-op → 디스크 I/O 0.
    # mkdir 직후 attach — 즉시 발생하는 ensure_schema / LangGraph 빌드 / step 로그 다 포함.
    file_handlers = attach_run_file_handlers(run.run_dir)
    run_started_at = time.monotonic()
    log_info(
        "run",
        "start",
        root=str(codebase.root),
        strategy=getattr(strategy, "name", "?"),
        run_id=run.run_id,
    )
    try:
        # 호출자가 대상 graph 를 정하면 그것을, 아니면 ENV 를 쓴다. **아래 wipe 의
        # 범위가 이 한 줄로 정해진다** — 대상이 주변 환경에서 조용히 정해지면
        # 한 프로젝트를 분석했을 뿐인데 다른 프로젝트의 결과가 사라진다.
        target_db = database or ENV.neo4j.database
        neo_client = Neo4jClient(target_db)
        try:
            # 매 run 시작 — 옛 노드/엣지 wipe (테스트 정책: 깨끗 상태).
            # schema (제약/인덱스) 는 wipe 무관 — ensure_schema 가 idempotent.
            await wipe_graph(neo_client)
            await ensure_schema(neo_client)
            log_info("run", "neo4j_ready", database=target_db, wiped=True,
                     from_request=bool(database))

            cost_budget = CostBudget()
            checkpointer = MemorySaver()
            app = build_langgraph_workflow(strategy, checkpointer=checkpointer)

            # 병렬 그룹(후처리) 공유 LLM 세마포어 — step 마다 따로 Semaphore(30) 를 만들면
            # 동시 실행 시 30×N 이 한 서버로 몰려 과부하. 하나를 공유해 총 동시수를 묶는다.
            # 그룹 아닌(순차) step 도 같은 세마포어를 써도 결과 동일(동시엔 1 step 만 활성).
            llm_semaphore = asyncio.Semaphore(ENV.pipeline.max_concurrency)

            # 일시정지/재개/중단 컨트롤러 — control 엔드포인트가 registry 로 찾아 신호.
            pause = PauseController(stream)
            set_active_run(run.run_id, pause)

            initial_state: dict = {"run": run}
            config = {
                "configurable": {
                    # LangGraph Checkpointer 가 요구하는 키 — run_id 를 그대로 쓴다.
                    "thread_id": run.run_id,
                    "strategy": strategy,
                    "codebase": codebase,
                    "run": run,
                    "cost_budget": cost_budget,
                    "stream": stream,
                    "neo_client": neo_client,
                    "pause": pause,
                    "llm_semaphore": llm_semaphore,   # 후처리 병렬 그룹 공유 동시성 한도
                }
            }

            if stream is not None:
                # emit 안에서 logger mirror 가 자동으로 analyzer.log 에 phase_event 한 줄.
                stream.emit({"type": "phase_event", "event": "start", "run_id": run.run_id})

            # step 경계마다 pause.gate() — paused 면 다음 step 앞에서 hold, stopped 면
            # RunStopped. stop 은 사용자 의도된 종료라 실패가 아님 → 정상 경로로 마무리.
            stopped = False
            try:
                async for chunk in app.astream(initial_state, config=config, stream_mode="updates"):
                    if isinstance(chunk, dict):
                        for node_name in chunk:
                            if stream is not None:
                                stream.emit({"type": "phase_event", "event": "node_done", "node": node_name})
                    await pause.gate()
            except Exception:
                # stopped 플래그가 켜진 채 빠져나온 예외 = 사용자 중단(RunStopped / 취소된
                # in-flight 의 래핑). 그 외 예외는 진짜 실패 → 그대로 위로 전파.
                if not pause.status()["isStopped"]:
                    raise
                stopped = True
                if stream is not None:
                    stream.emit({"type": "phase_event", "event": "stopped", "run_id": run.run_id})
                log_info("run", "stopped", run_id=run.run_id)

            if not stopped:
                # 최종 state = LangGraph 가 reducer(operator.add)까지 적용한 누적 state.
                # ★ astream "updates" 델타를 dict.update 로 모으면 operator.add 키
                #   (reads_writes_edges / call_sites 등)가 덮어써져 artifact 가 불완전해진다
                #   — checkpointer 의 reduced state 를 단일 진실로 쓴다.
                final_state = dict((await app.aget_state(config)).values)
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
                    stream.emit({"type": "phase_event", "event": "complete", "run_id": run.run_id})

                log_info(
                    "run",
                    "complete",
                    run_id=run.run_id,
                    elapsed_sec=f"{time.monotonic() - run_started_at:.1f}",
                )

        finally:
            clear_active_run(run.run_id)
            await neo_client.close()
    except Exception as e:
        # 파이프라인 실패 — 스트림 consumer 가 알도록 에러 emit (조용한 실패 차단).
        log_error(
            "run", "run_failed", e,
            stream_emit=stream.emit if stream is not None else None,
            run_id=run.run_id,
        )
        raise
    finally:
        # neo close 다음 — Windows fd 충돌 회피 위해 attach 의 역순.
        detach_run_file_handlers(file_handlers)
        # 성공·실패 무관 스트림 종료 — 안 닫으면 consumer 가 stream_end 를 영구 대기 (hang).
        if stream is not None:
            await stream.close()
    return run


def _reset_logs_root(log_base: Path) -> None:
    """매 실행 ``logs/`` 통째 rmtree 후 재생성 — 옛 run 잔재로 인한 오해 차단.

    옛 정책 (``analysis.run/latest`` + ``llm_prompts/latest`` 만 reset) 은 ``logs/`` 직속의
    임시 파일 (``_latest_run_console.log`` 등) 또는 timestamp 폴더 잔재가 남을 수 있음 →
    monitor 가 옛 inode 잡고 옛 라인 토해내는 사고 발생 → ``logs/`` 통째 정리.
    """
    if log_base.exists():
        shutil.rmtree(log_base, ignore_errors=True)
    log_base.mkdir(parents=True, exist_ok=True)
    (log_base / "analysis.run" / "latest").mkdir(parents=True, exist_ok=True)
    (log_base / "llm_prompts" / "latest").mkdir(parents=True, exist_ok=True)


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
