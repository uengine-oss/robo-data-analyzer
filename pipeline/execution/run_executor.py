"""Run 1회 실행 어댑터 — rmtree latest + LangGraph workflow 실행 + 종료 처리.

흐름:
1. 선택된 run-output 루트를 초기화한다. 미지정 시 기존 기본 ``logs/``를 사용한다.
2. ``Run`` 메타 생성 + Neo4j 인덱스 / 제약 보장.
3. ``CostBudget`` + ``MemorySaver`` (Checkpointer) + ``PauseController`` 생성.
4. Strategy 의 workflow 빌더 호출 → ``astream(stream_mode="updates")``.
5. ``usage_summary.json`` 집계.

**run 정책** — 매 ``/analyze`` 호출 = 독립 run (``run_id`` 가 유일 식별자). 진행은 NDJSON stream.

**일시정지 / 재개 / 중단** (소프트 — 프로세스가 살아있는 동안의 hold):
- ``PauseController`` 를 만들어 ``run_registry`` 에 등록 → control 엔드포인트가 신호.
- astream 소비 루프가 step 경계마다 ``pause.gate()`` 로 소비(paused 면 hold).
- stop 은 ``RunStopped`` 로 정상 종료(실패 아님). 서버를 끄면 run 도 끝난다(디스크 재개 X).
- Checkpointer(MemorySaver)는 run 1회 안의 state 전파용 — wipe / thread_id 정책 그대로.
"""
from __future__ import annotations

import dataclasses
import hashlib
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
from shared.config.settings import SETTINGS

from llm import response_cache
from llm.usage.cost_budget import CostBudget
from shared.events.runtime_logger import (
    attach_run_file_handlers,
    detach_run_file_handlers,
    log_error,
    log_info,
)
from shared.events.ndjson_event_stream import NDJSONStream
from graph.neo4j_client import Neo4jClient
from graph.schema.constraints import ensure_schema, wipe_graph
from graph.connection_context import get_override
from shared.files.cache import SourceFileCache
from shared.concurrency.priority_semaphore import PrioritySemaphore
from pipeline.execution.analysis_handoff import AnalysisHandoff
from pipeline.execution.langgraph_builder import build_langgraph_workflow
from pipeline.execution.pause_controller import PauseController
from pipeline.execution.run_registry import clear as clear_active_run
from pipeline.execution.run_registry import set_active as set_active_run
from pipeline.execution.run_registry import ConcurrentAnalysisRunError
from pipeline.execution.run_output_context import (
    reset_response_locale,
    reset_run_dir,
    set_response_locale,
    set_run_dir,
)


_RUN_OUTPUT_OWNER_MARKER = ".robo-data-analyzer-output"
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
    run_output_root: Path | None = None,
) -> AnalysisRun:
    """AnalysisRun 1회 실행 — 매 호출이 독립 run (``run_id`` 가 유일 식별자).

    Args:
        strategy: ``FRAMEWORK_PIPELINE`` 또는 ``DBMS_PIPELINE`` (``analysis_pipelines``).
        codebase: 분석 대상.
        stream: 프론트 NDJSON 스트림.
    """
    run_output_root = run_output_root or SETTINGS.run_output.dir
    run = AnalysisRun.now(codebase=codebase, run_output_root=run_output_root)
    pause = PauseController(stream)

    try:
        # 단일 활성 슬롯은 output reset/Neo4j wipe보다 먼저 선점한다. 두 번째 run이
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

    output_context_token = None
    locale_context_token = None
    try:
        _reset_run_output_root(run_output_root)
        output_context_token = set_run_dir(run.run_dir)
        locale_context_token = set_response_locale(codebase.locale)
        _write_initial_manifest(run, strategy)

        # ── 파일 핸들러 attach (ROBO_RUNTIME_EVENT_FILE_ENABLED=true 일 때만) ──
        # analyzer.log (INFO+) + error.log (WARNING+) → tail -f 로 실시간 추적 가능.
        # 운영기 OFF → 빈 리스트 → detach no-op → 디스크 I/O 0.
        # mkdir 직후 attach — 즉시 발생하는 ensure_schema / LangGraph 빌드 / step 로그 다 포함.
        file_handlers = attach_run_file_handlers(run.run_dir)
    except BaseException:
        if locale_context_token is not None:
            reset_response_locale(locale_context_token)
        if output_context_token is not None:
            reset_run_dir(output_context_token)
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
            log_info("run", "neo4j_ready", database=_resolved_neo4j_database(), wiped=True)

            # 응답 캐시 정리 — 프롬프트를 고칠 때마다 다시는 안 읽히는 항목이 쌓인다.
            # run 시작 1회, 상한 초과 시에만 실제로 지운다.
            response_cache.sweep()

            cost_budget = CostBudget()
            # 체크포인트(msgpack)가 직렬화하는 state dataclass(CodeNode/Table/Rule/Calls 등)를
            # 명시 allowlist 로 등록 — langgraph 가 "미등록 타입" 경고 없이 조용히 역직렬화하고,
            # 장래 STRICT 차단에도 안전. (=True 는 "전체허용+경고"라 근본 해결 아님.)
            checkpointer = MemorySaver(
                serde=JsonPlusSerializer(allowed_msgpack_modules=_STATE_MSGPACK_ALLOWLIST)
            )
            app = build_langgraph_workflow(strategy, checkpointer=checkpointer)

            # run 전체가 공유하는 **단 하나의** LLM 슬롯 (spec 096). step 마다 따로 만들면
            # 동시 실행 시 N 배가 한 서버로 몰려 과부하 — 실측으로 닫힌 길이다.
            # 우선순위 세마포어인 이유: step7 과 후처리가 이제 같은 superstep 에서 겹치므로
            # 총량은 그대로 두되 임계경로(step7)가 항상 먼저 슬롯을 잡아야 한다.
            llm_semaphore = PrioritySemaphore(SETTINGS.analysis_pipeline.max_concurrency)
            # step7 → 후처리 인계 채널 — 분할이 확정된 시점을 후처리에 알려 배리어를 없앤다.
            analysis_handoff = AnalysisHandoff(llm_semaphore)

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
                    "llm_semaphore": llm_semaphore,   # run 공유 동시성 한도 (총량 단일 진실)
                    "analysis_handoff": analysis_handoff,  # step7 분할 확정 → 후처리 인계
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
                    # 실패해도 사용량 집계는 남긴다 (state artifact 덤프는 사용자 지시로 제거
                    # — 선택된 logs 루트에는 로그·jsonl·llm-prompts만 둔다).
                    try:
                        _summarize_usage(run.run_dir)
                        log_info("run", "failure_usage_summarized", run_dir=str(run.run_dir))
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
        if output_context_token is not None:
            reset_run_dir(output_context_token)
        if locale_context_token is not None:
            reset_response_locale(locale_context_token)
        # 성공·실패 무관 스트림 종료 — 안 닫으면 consumer 가 stream_end 를 영구 대기 (hang).
        if stream is not None:
            await stream.close()
    return run


def _reset_run_output_root(run_output_root: Path) -> None:
    """Analyzer가 소유한 선택된 실행 결과 루트만 초기화한다.

    기본값은 기존 ``logs/``다. ``--run-output``이 명시된 경우에만 그 경로를 같은
    계약으로 사용한다. 자동 하위 run 디렉터리나 백업은 만들지 않는다.
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
    marker = root / _RUN_OUTPUT_OWNER_MARKER
    marker.write_text(_RUN_OUTPUT_OWNER_MARKER_CONTENT, encoding="utf-8")
    (root / "llm-prompts").mkdir(parents=True, exist_ok=True)


def _prompt_tree_sha256(prompts_root: Path) -> str:
    """prompt 상대경로와 바이트를 안정 순서로 해시한다."""
    digest = hashlib.sha256()
    for path in sorted(
        (item for item in prompts_root.rglob("*") if item.is_file()),
        key=lambda item: item.relative_to(prompts_root).as_posix(),
    ):
        relative = path.relative_to(prompts_root).as_posix().encode("utf-8")
        payload = path.read_bytes()
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        digest.update(len(payload).to_bytes(8, "big"))
        digest.update(payload)
    return digest.hexdigest()


def _resolved_neo4j_database() -> str:
    override = get_override()
    return (
        override.database
        if override is not None and override.database
        else SETTINGS.graph_database.database
    )


def _write_initial_manifest(run: AnalysisRun, strategy: Any) -> None:
    """그래프 변경/LLM 호출 전에 현행 실행의 입력 정체성과 prompt hash를 기록한다."""
    repository_root = Path(__file__).resolve().parents[2]
    strategy_prompts = Path(getattr(strategy, "prompts_dir", "llm/prompts"))
    if not strategy_prompts.is_absolute():
        strategy_prompts = repository_root / strategy_prompts
    prompts_root = strategy_prompts.resolve().parent
    if prompts_root.name != "prompts":
        prompts_root = (repository_root / "llm" / "prompts").resolve()

    source_root = run.codebase.root.resolve()
    corpus_root = (run.codebase.corpus_root or run.codebase.root).resolve()
    ddl_root = run.codebase.ddl_dir.resolve() if run.codebase.ddl_dir else None
    manifest = {
        "schema_version": "1.0",
        "run_id": run.run_id,
        "started_at": run.started_at.isoformat(),
        "corpus_root": str(corpus_root),
        "source_root": str(source_root),
        "ddl_root": str(ddl_root) if ddl_root else None,
        "strategy": run.codebase.strategy,
        "locale": run.codebase.locale,
        "neo4j_database": _resolved_neo4j_database(),
        "run_output_root": str(run.run_dir.resolve()),
        "run_dir": str(run.run_dir.resolve()),
        "llm_config": SETTINGS.analysis_llm.config,
        "prompt_tree_sha256": _prompt_tree_sha256(prompts_root),
    }
    manifest_path = run.run_dir / "manifest.json"
    with manifest_path.open("x", encoding="utf-8") as file:
        json.dump(manifest, file, ensure_ascii=False, indent=2)
        file.write("\n")


def _summarize_usage(run_dir: Path) -> None:
    """``usage.jsonl`` → ``usage_summary.json`` 집계."""
    usage_path = run_dir / "usage.jsonl"
    if not usage_path.exists():
        return

    # ``response_cache_hits`` — 응답 캐시(spec 091)가 답을 돌려준 호출 수. 적중도 ``calls``
    # 에 포함되므로 이 값이 없으면 "호출은 늘었는데 토큰은 0" 으로 보여 집계가 오해를 만든다.
    per_agent_model: dict[tuple[str, str], dict] = defaultdict(lambda: {
        "input_tokens": 0,
        "output_tokens": 0,
        "cached_tokens": 0,
        "total_tokens": 0,
        "cost_usd": 0.0,
        "calls": 0,
        "response_cache_hits": 0,
    })
    total = {
        "input_tokens": 0,
        "output_tokens": 0,
        "cached_tokens": 0,
        "total_tokens": 0,
        "cost_usd": 0.0,
        "calls": 0,
        "response_cache_hits": 0,
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
            if record.get("response_cache_hit"):
                bucket["response_cache_hits"] += 1
                total["response_cache_hits"] += 1

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

    # 캐시가 일하는지 헛도는지 한 줄로 보인다 — 적중률이 뜻밖의 값이면 키가 깨진 것이다.
    calls = total["calls"]
    hits = total["response_cache_hits"]
    log_info(
        "llm.response_cache",
        "hit_rate",
        calls=calls,
        hits=hits,
        rate=f"{(hits / calls * 100) if calls else 0:.1f}%",
    )
