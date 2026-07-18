"""Run 1회 실행 어댑터 — rmtree latest + LangGraph workflow 실행 + 종료 처리.

흐름:
1. ``run-output/analysis/latest/`` + ``run-output/llm-prompts/latest/`` rmtree + 재생성.
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
import dataclasses
import json
import shutil
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Optional

from langgraph.checkpoint.memory import MemorySaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

from graph.schema import graph_edges, graph_nodes
from pipeline.models import analysis_context
from pipeline.models.analysis_meta import AnalysisRun
from pipeline.models.analysis_meta import Codebase
from analyzer_settings import SETTINGS

from llm.usage.cost_budget import CostBudget
from runtime_events.runtime_logger import (
    attach_run_file_handlers,
    detach_run_file_handlers,
    log_error,
    log_info,
)
from runtime_events.ndjson_event_stream import NDJSONStream
from graph.neo4j_client import Neo4jClient
from graph.schema.constraints import ensure_schema, wipe_graph
from source.file_cache import SourceFileCache
from pipeline.execution.artifact_writer import dump_state_artifacts
from pipeline.execution.langgraph_builder import build_langgraph_workflow
from pipeline.execution.pause_controller import PauseController
from pipeline.execution.run_registry import clear as clear_active_run
from pipeline.execution.run_registry import set_active as set_active_run
from pipeline.execution.run_registry import ConcurrentAnalysisRunError


_RUN_OUTPUT_OWNER_MARKER = ".robo-data-analyzer-run-output"
_RUN_OUTPUT_OWNER_MARKER_CONTENT = "robo-data-analyzer\n"


def _state_msgpack_allowlist() -> tuple[type, ...]:
    """체크포인트(MemorySaver·msgpack)에 직렬화되는 state dataclass 전부 — langgraph
    역직렬화 명시 allowlist.

    ``graph_nodes`` / ``graph_edges`` 에서 dataclass 를 자동 수집(새 노드/엣지 추가 시 자동
    등록 — 누락 0) + 컨텍스트 / 코드베이스 / run. ``allowed_msgpack_modules=True`` 는
    "전체 허용 + 미등록 경고"라 근본 해결이 아님 — 명시 목록이라야 경고 0 + 장래 STRICT 차단에도 안전.
    """
    types = [
        obj
        for mod in (graph_nodes, graph_edges)
        for obj in vars(mod).values()
        if isinstance(obj, type) and dataclasses.is_dataclass(obj)
    ]
    types += [
        obj
        for obj in vars(analysis_context).values()
        if isinstance(obj, type) and dataclasses.is_dataclass(obj)
    ]
    types += [Codebase, AnalysisRun]
    return tuple(types)


# import 시 1회 — 직렬화기 allowlist (run 마다 재계산 불필요).
_STATE_MSGPACK_ALLOWLIST = _state_msgpack_allowlist()


async def execute_run(
    strategy: Any,
    codebase: Codebase,
    stream: Optional[NDJSONStream] = None,
) -> AnalysisRun:
    """AnalysisRun 1회 실행 — 매 호출이 독립 run (``run_id`` 가 유일 식별자).

    Args:
        strategy: ``FRAMEWORK_PIPELINE`` 또는 ``DBMS_PIPELINE`` (``analysis_pipelines``).
        codebase: 분석 대상.
        stream: 프론트 NDJSON 스트림.
    """
    run_output_root = SETTINGS.run_output.dir
    run = AnalysisRun.now(codebase=codebase, run_output_root=run_output_root)
    pause = PauseController(stream)

    try:
        # 단일 활성 슬롯은 run-output reset/Neo4j wipe보다 먼저 선점한다. 두 번째 run이
        # 파괴 작업에 진입한 뒤 control 슬롯만 덮어쓰는 경쟁을 막는다.
        set_active_run(run.run_id, pause)
    except ConcurrentAnalysisRunError as error:
        if stream is not None:
            stream.emit({
                "type": "error",
                "component": "run.registry",
                "operation": "claim_active_run",
                "message": str(error),
            })
            await stream.close()
        raise
    except BaseException:
        if stream is not None:
            await stream.close()
        raise

    try:
        _reset_run_output_root(run_output_root)
        run.run_dir.mkdir(parents=True, exist_ok=True)
        (run.run_dir / "artifacts").mkdir(parents=True, exist_ok=True)

        # ── 파일 핸들러 attach (ROBO_RUNTIME_EVENT_FILE_ENABLED=true 일 때만) ──
        # analyzer.log (INFO+) + error.log (WARNING+) → tail -f 로 실시간 추적 가능.
        # 운영기 OFF → 빈 리스트 → detach no-op → 디스크 I/O 0.
        # mkdir 직후 attach — 즉시 발생하는 ensure_schema / LangGraph 빌드 / step 로그 다 포함.
        file_handlers = attach_run_file_handlers(run.run_dir)
    except BaseException:
        clear_active_run(run.run_id)
        if stream is not None:
            await stream.close()
        raise

    run_started_at = time.monotonic()
    try:
        log_info(
            "run",
            "start",
            root=str(codebase.root),
            strategy=getattr(strategy, "name", "?"),
            run_id=run.run_id,
        )
        neo_client = Neo4jClient()
        try:
            # 매 run 시작 — 옛 노드/엣지 wipe (테스트 정책: 깨끗 상태).
            # schema (제약/인덱스) 는 wipe 무관 — ensure_schema 가 idempotent.
            await wipe_graph(neo_client)
            await ensure_schema(neo_client)
            log_info("run", "neo4j_ready", database=SETTINGS.graph_database.database, wiped=True)

            cost_budget = CostBudget()
            # 체크포인트(msgpack)가 직렬화하는 state dataclass(CodeNode/Table/Rule/Calls 등)를
            # 명시 allowlist 로 등록 — langgraph 가 "미등록 타입" 경고 없이 조용히 역직렬화하고,
            # 장래 STRICT 차단에도 안전. (=True 는 "전체허용+경고"라 근본 해결 아님.)
            checkpointer = MemorySaver(
                serde=JsonPlusSerializer(allowed_msgpack_modules=_STATE_MSGPACK_ALLOWLIST)
            )
            app = build_langgraph_workflow(strategy, checkpointer=checkpointer)

            # 병렬 그룹(후처리) 공유 LLM 세마포어 — step 마다 따로 Semaphore(30) 를 만들면
            # 동시 실행 시 30×N 이 한 서버로 몰려 과부하. 하나를 공유해 총 동시수를 묶는다.
            # 그룹 아닌(순차) step 도 같은 세마포어를 써도 결과 동일(동시엔 1 step 만 활성).
            llm_semaphore = asyncio.Semaphore(SETTINGS.analysis_pipeline.max_concurrency)

            # 일시정지/재개/중단 컨트롤러 — control 엔드포인트가 registry 로 찾아 신호.
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
                    # ★ run 스코프 소스 캐시 (spec 046 FR-008) — 파일당 디스크 read 1회.
                    #   종전엔 step 마다 새 인스턴스를 만들어 캐시가 무력화됐고, 같은 파일을
                    #   한 run 에서 5~8회 읽었다 (step2 안에서만 2회).
                    #   run 스코프라 "장수명 서버의 stale" 위험(SourceFileCache docstring)도 없다.
                    "source_cache": SourceFileCache(),
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
                    # LangGraph checkpointer에는 실패 직전까지 완료된 step state가 남아 있다.
                    # 원래 pipeline 오류를 가리지 않는 best-effort 경로로 step1~6 산출물을
                    # 먼저 보존한다. 특히 step7 외부 연동 실패가 수시간 분석을 지우지 않게 한다.
                    try:
                        await _dump_checkpoint_artifacts(app, config, run)
                        _summarize_usage(run.run_dir)
                        log_info("run", "failure_artifacts_dumped", run_dir=str(run.run_dir))
                    except Exception as artifact_error:
                        log_error(
                            "run", "dump_failure_artifacts_failed", artifact_error,
                            stream_emit=stream.emit if stream is not None else None,
                            run_id=run.run_id,
                        )
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
                # artifacts/ 4파일 — 실패 시 명시 emit + 상위로 전파 (silent X)
                try:
                    await _dump_checkpoint_artifacts(app, config, run)
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
            await neo_client.close()
    except Exception as e:
        # 파이프라인 실패 — 스트림 consumer 가 알도록 에러 emit (조용한 실패 차단).
        log_error(
            "run", "run_failed", e,
            stream_emit=stream.emit if stream is not None else None,
            run_id=run.run_id,
            fatal=True,
        )
        raise
    finally:
        clear_active_run(run.run_id)
        # neo close 다음 — Windows fd 충돌 회피 위해 attach 의 역순.
        detach_run_file_handlers(file_handlers)
        # 성공·실패 무관 스트림 종료 — 안 닫으면 consumer 가 stream_end 를 영구 대기 (hang).
        if stream is not None:
            await stream.close()
    return run


async def _dump_checkpoint_artifacts(app: Any, config: dict, run: AnalysisRun) -> dict:
    """최신 reduced checkpoint state를 artifact에 보존하고 그 state를 반환한다."""
    state = dict((await app.aget_state(config)).values)
    dump_state_artifacts(run, state)
    return state


def _reset_run_output_root(run_output_root: Path) -> None:
    """Analyzer가 소유한 실행 결과 루트만 초기화한다.

    최초에는 없거나 빈 디렉터리만 소유권 표식과 함께 인수한다. 이후에는 표식 내용까지
    일치해야만 전체 삭제한다. 경로 오설정이나 이름 우연 일치로 사용자 파일을 지우지 않는다.
    """
    if not isinstance(run_output_root, Path):
        raise ValueError(f"run_output_root must be a Path; got {run_output_root!r}")
    if run_output_root.is_symlink():
        raise ValueError(f"실행 결과 루트는 심볼릭 링크일 수 없습니다: {run_output_root}")

    root = run_output_root.resolve()
    if root == Path.cwd().resolve():
        raise ValueError(f"실행 결과 루트는 현재 작업 디렉터리일 수 없습니다: {root}")
    if root == Path(root.anchor):
        raise ValueError(f"실행 결과 루트는 파일시스템 루트일 수 없습니다: {root}")
    if root.exists() and not root.is_dir():
        raise ValueError(f"실행 결과 루트가 디렉터리가 아닙니다: {root}")

    marker = root / _RUN_OUTPUT_OWNER_MARKER
    if root.exists() and any(root.iterdir()):
        if not marker.is_file():
            raise ValueError(f"Analyzer가 소유하지 않은 비어 있지 않은 디렉터리입니다: {root}")
        try:
            marker_content = marker.read_text(encoding="utf-8")
        except OSError as exc:
            raise ValueError(f"실행 결과 소유권 표식을 읽을 수 없습니다: {marker}") from exc
        if marker_content != _RUN_OUTPUT_OWNER_MARKER_CONTENT:
            raise ValueError(f"실행 결과 소유권 표식이 올바르지 않습니다: {marker}")
        # 정리에 실패한 옛 로그를 새 run의 결과로 오인하지 않도록 오류를 전파한다.
        shutil.rmtree(root)

    root.mkdir(parents=True, exist_ok=True)
    marker.write_text(_RUN_OUTPUT_OWNER_MARKER_CONTENT, encoding="utf-8")
    (root / "analysis" / "latest").mkdir(parents=True, exist_ok=True)
    (root / "llm-prompts" / "latest").mkdir(parents=True, exist_ok=True)


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
