"""processing_event 진행 가시성 — cross-cutting concern (비즈니스 로직 분리).

설계
─────────────
LangGraph step 안 "지금 누구를 처리 중" (코드노드 / 모듈 / 테이블 / split) 진행도는
비즈니스 함수 시그니처를 어지럽히면 안 된다 (stream 인자 chain 금지). 대신:

1. **step 진입 시 1 회** ``with step_processing_scope(step, stream)`` —
   contextvars 에 stream + step 박음 (그 안 모든 ``track_processing`` 이 자동 캐치).
2. **각 처리 단위 1 줄** ``async with track_processing("CodeNode", fn_id)`` —
   start/done/failed 자동 emit. 비즈니스 함수는 stream 인자 모름.

영역 분리 (기존 NDJSON 이벤트와 중복 0)
─────────────────────────────────────
| ``phase_event``         | step 전체 (executor 가 emit)              |
| ``tool_event``          | Agent 도구 호출 (Scan/Residual 미들웨어)    |
| ``node_event`` / ``relationship_event`` | 그래프 결과 노드/엣지 (완료) |
| **``processing_event`` (이 모듈)** | step 안 처리 단위 진행 (코드노드 / 모듈 / 테이블 / split) |

contextvars 안전성
──────────────────
- Python 3.7+ 표준 + asyncio 친화 (gather / create_task / as_completed 모두 컨텍스트 복제 → task 격리)
- 같은 패턴이 ``llm/request_tracing.py`` 의 ``_invoke_var`` 에 이미 사용 — 코드베이스 일관
- ``step_processing_scope`` 의 ``try/finally`` 가 Token reset 보장 — 다음 run 잔재 0
- stream 미연결 (CLI 단독) 시 ``_current_stream.get() = None`` → emit no-op (안전)

호출 정책 (★ track_processing 의 fail = raise 만)
─────────────────────────────────────────────
``track_processing`` 안 block 의 fail 신호는 ``raise`` 가 단일 진입.
- ``raise`` → ``failed`` emit 후 re-raise (caller 가 받음)
- ``return`` → ``done`` emit (정상)

호출자가 fail 을 ``return`` 으로 정상화하면 ``done`` 으로 잘못 emit 되므로
**fail = 반드시 raise**. caller (보통 fan-out 메인) 이 ``return_exceptions=True``
로 받아 분석 흐름 유지.
"""
from __future__ import annotations

from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
from typing import Optional

from shared.events.ndjson_event_stream import NDJSONStream


_current_step: ContextVar[str] = ContextVar("processing_current_step", default="")
_current_stream: ContextVar[Optional[NDJSONStream]] = ContextVar(
    "processing_current_stream", default=None,
)


@contextmanager
def step_processing_scope(step: str, stream: Optional[NDJSONStream]):
    """LangGraph step 진입 시 1 회 set — 그 블록 안 모든 ``track_processing`` 이 자동 사용.

    Args:
        step: 영역명 — ``"step7.analyze_code"`` / ``"step8.summarize_module"`` 등.
        stream: NDJSONStream. CLI 단독 (stream 미연결) 이면 None — ``track_processing`` 이 no-op.

    Usage::

        async def my_step(state, config):
            cfg = step_config(config)
            with step_processing_scope("step7.analyze_code", cfg.get("stream")):
                # 이 블록 안 모든 track_processing 이 자동으로 step + stream 캐치
                ...

    block 종료 시 정상/예외 무관 reset 보장 (다음 run 잔재 0).
    """
    step_token = _current_step.set(step)
    stream_token = _current_stream.set(stream)
    try:
        yield
    finally:
        _current_step.reset(step_token)
        _current_stream.reset(stream_token)


@asynccontextmanager
async def track_processing(entity_kind: str, entity_id: str, *, sub_action: str = ""):
    """처리 단위 wrap — start emit → block 실행 → done/failed 자동 emit.

    Args:
        entity_kind: 처리 대상 종류 — ``"CodeNode"`` / ``"Module"`` / ``"Table"`` / ``"Split"``.
        entity_id: 처리 대상 식별자 — code_id / module_id / table_fqn 등.
        sub_action: 세부 액션 (선택) — ``"split_summary"`` / ``"split_rules"`` / ``"one_liner"`` 등.

    Usage::

        async with track_processing("CodeNode", fn_id):
            return await analyze_code_three_split(...)

        async with track_processing("CodeNode", fn_id, sub_action="split_rules"):
            return await run_yaml_rule(...)

    예외 발생 시 ``failed`` emit 후 re-raise (silent 차단 X).
    ★ fail = raise 단일 진입. return 으로 정상화하지 말 것 (잘못된 done emit).
    """
    step = _current_step.get() or "(unknown)"
    stream = _current_stream.get()
    emit_processing_event(stream, step, entity_kind, entity_id, sub_action=sub_action, status="start")
    try:
        yield
    except BaseException:
        emit_processing_event(stream, step, entity_kind, entity_id, sub_action=sub_action, status="failed")
        raise
    emit_processing_event(stream, step, entity_kind, entity_id, sub_action=sub_action, status="done")


def emit_current(
    entity_kind: str,
    entity_id: str,
    *,
    sub_action: str = "",
    status: str = "done",
) -> None:
    """contextvars 흐름 안에서 단발 processing_event emit (start/done 쌍이 아닌 마일스톤용).

    ``track_processing`` 은 await 를 감싸 start→done/failed 를 낸다. 폴백의 감지·분할계획·
    재검사·최종요약처럼 **await 가 아닌 결정 시점**을 스트림에 남길 때 이 헬퍼를 쓴다 —
    현재 step/stream 을 contextvars 에서 읽어 emit(스트림 인자 chain 금지 설계 유지).
    """
    emit_processing_event(
        _current_stream.get(), _current_step.get() or "(unknown)",
        entity_kind, entity_id, sub_action=sub_action, status=status,
    )


def emit_processing_event(
    stream: Optional[NDJSONStream],
    step: str,
    entity_kind: str,
    entity_id: str,
    *,
    sub_action: str = "",
    status: str = "start",
) -> None:
    """processing_event NDJSON 1 건 emit — ``track_processing`` 의 기본 진입.

    호출처가 contextvars 흐름 밖이거나 (executor 시작 직전 등) 명시적 단발 emit 이
    필요할 때 직접 호출. 일반 케이스는 ``track_processing`` 권장 (start/done/failed 자동).
    """
    if stream is None:
        return
    stream.emit({
        "type": "processing_event",
        "step": step,
        "entity_kind": entity_kind,
        "entity_id": entity_id,
        "sub_action": sub_action,
        "status": status,
    })
