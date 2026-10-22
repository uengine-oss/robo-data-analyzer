"""Run 1회 실행 어댑터 — rmtree latest + LangGraph workflow 실행 + 종료 처리.

흐름:
1. ``logs/analysis.run/latest/`` + ``logs/llm_prompts/latest/`` rmtree + 재생성.
2. ``Run`` 메타 생성 + Neo4j 인덱스 / 제약 보장.
3. ``CostBudget`` + ``MemorySaver`` (Checkpointer) 생성.
4. Strategy 의 workflow 빌더 호출 → ``astream(stream_mode="updates")``.
5. 산출물 4파일 (artifacts/) + ``usage_summary.json``.

**run 정책 (Plan §4 M1)** — 매 ``/analyze`` 호출 = 독립 run (``run_id`` 가 유일 식별자):
- 별도 컨트롤러 / control endpoint 없음 — 진행은 NDJSON stream.
- resume(중단된 run 재개)은 현재 미지원 — 진짜 프론트가 필요로 할 때 도입.
  Checkpointer(MemorySaver)는 run 1회 안의 state 전파용으로만 둔다.
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
from shared.neo4j.catalog_projection import project_to_catalog
from shared.neo4j.neo4j_client import Neo4jClient
from shared.neo4j.schema_constraints import ensure_schema, wipe_graph
from shared.workflow_runner.artifact_dumper import dump_state_artifacts
from shared.workflow_runner.langgraph_workflow import build_langgraph_workflow


async def execute_run(
    strategy: Any,
    project: Project,
    stream: Optional[NDJSONStream] = None,
) -> Run:
    """Run 1회 실행 — 매 호출이 독립 run (``run_id`` 가 유일 식별자).

    Args:
        strategy: ``FRAMEWORK_PIPELINE`` 또는 ``DBMS_PIPELINE`` (``analysis_pipelines``).
        project: 분석 대상.
        stream: 프론트 NDJSON 스트림.
    """
    log_base = ENV.log.dir
    _reset_logs_root(log_base)

    run = Run.now(project=project, log_base=log_base)
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
        project=project.name,
        strategy=getattr(strategy, "name", "?"),
        run_id=run.run_id,
    )
    try:
        neo_client = Neo4jClient()
        try:
            # 매 run 시작 — 옛 노드/엣지 wipe (테스트 정책: 깨끗 상태).
            # schema (제약/인덱스) 는 wipe 무관 — ensure_schema 가 idempotent.
            await wipe_graph(neo_client)
            await ensure_schema(neo_client)
            log_info("run", "neo4j_ready", database=ENV.neo4j.database, wiped=True)

            cost_budget = CostBudget()
            checkpointer = MemorySaver()
            app = build_langgraph_workflow(strategy, checkpointer=checkpointer)

            initial_state: dict = {"run": run}
            config = {
                "configurable": {
                    # LangGraph Checkpointer 가 요구하는 키 — run_id 를 그대로 쓴다.
                    "thread_id": run.run_id,
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
                stream.emit({"type": "phase_event", "event": "start", "run_id": run.run_id})

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

            # 분석으로 알아낸 설명을 카탈로그 노드에 투영한다.
            #
            # 분석기는 자기 라벨(`:TABLE`/`:COLUMN`)에만 쓰고, 카탈로그를 읽는 쪽은
            # `:Table`/`:Column` 을 읽는다. 이 단계가 없으면 **분석 결과가 온톨로지 구축과
            # 자연어 질의에 영영 보이지 않는다.** 자세한 경위는 catalog_projection.py 참조.
            #
            # 투영 실패로 분석 전체를 실패시키지는 않는다 — 분석 산출물은 이미 저장됐고,
            # 카탈로그가 아직 추출되지 않은 상태(대응 노드 없음)도 정상이기 때문이다.
            # 다만 **조용히 넘기지 않는다.** 0건은 "카탈로그에 대응 노드가 없다"는 뜻이고,
            # 그 사실을 모르면 "증강했는데 테이블을 못 찾는다"로만 보인다.
            try:
                projected = await project_to_catalog(neo_client)
                log_info(
                    "run",
                    "catalog_projected",
                    tables=projected["tables"],
                    columns=projected["columns"],
                )
                if projected["tables"] == 0 and projected["columns"] == 0:
                    log_info(
                        "run",
                        "catalog_projection_empty",
                        hint="카탈로그에 대응 노드가 없습니다 — 데이터소스 메타데이터 추출을 먼저 실행하세요",
                    )
            except Exception as e:
                log_error(
                    "run",
                    "catalog_projection_failed",
                    e,
                    stream_emit=stream.emit if stream is not None else None,
                    run_id=run.run_id,
                )

            if stream is not None:
                stream.emit({"type": "phase_event", "event": "complete", "run_id": run.run_id})

            log_info(
                "run",
                "complete",
                run_id=run.run_id,
                elapsed_sec=f"{time.monotonic() - run_started_at:.1f}",
            )

        finally:
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
