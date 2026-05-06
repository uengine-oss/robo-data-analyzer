"""NDJSON 스트리밍 유틸리티

NDJSON 스트리밍 이벤트 생성 및 처리 함수들.

이벤트 타입 (frontend 가 이 type 으로 분기):
- user_message: 사용자 콘솔용 자연어 메시지 (ProgressModal 콘솔 표시)
- system_log: 디버그/서버 로그용 (frontend 무시 — server log 만)
- data: 그래프 데이터
- error: 오류
- node_event / relationship_event: 그래프 변경 이벤트
- phase_event: 단계 진행 이벤트 (dynamicSteps 누적용)
- complete: 완료

채널 분리 정책:
- 사용자 친화 자연어 메시지 (예: "테이블 보강 중...") → emit_user_message
- 시스템 라벨 (예: "[PHASE:3:ACTOR_MERGE] queries=N") → emit_system_log
- 발화 시점에 채널 결정 (변환 layer X)
"""

import asyncio
import json
import uuid
import logging
from typing import Any, Callable, Optional, AsyncGenerator


def emit_bytes(payload: dict) -> bytes:
    """NDJSON 형식으로 바이트 생성"""
    return json.dumps(payload, default=str, ensure_ascii=False).encode("utf-8") + b"\n"


def emit_user_message(content: str) -> bytes:
    """사용자 콘솔용 자연어 메시지 — ProgressModal 콘솔에 표시.

    NDJSON type 은 `"message"` (frontend 호환 유지). 함수 이름이
    `emit_user_message` 인 이유는 backend 코드 레벨에서 `emit_system_log` 와
    구분하기 위함 — 발화 시점에 어느 채널인지 명확히 결정.
    """
    return emit_bytes({"type": "message", "content": content})


def emit_system_log(level: str, content: str) -> bytes:
    """디버그/서버 로그용 — frontend 무시 (server log 만).

    사용처: 시스템 라벨 (`[PHASE:N:...]`), 메트릭 (`queries=N items=M`),
    내부 진단. 사용자 콘솔에 노출 X.
    """
    return emit_bytes({"type": "system_log", "level": level, "content": content})


def emit_error(
    content: str,
    error_type: Optional[str] = None,
    trace_id: Optional[str] = None,
) -> bytes:
    """에러 이벤트 전송"""
    payload = {"type": "error", "content": content}
    if error_type:
        payload["errorType"] = error_type
    if trace_id:
        payload["traceId"] = trace_id
    return emit_bytes(payload)


def emit_data(**fields) -> bytes:
    """데이터 이벤트 전송"""
    payload = {"type": "data"}
    payload.update({k: v for k, v in fields.items() if v is not None})
    return emit_bytes(payload)


def emit_complete(summary: Optional[str] = None) -> bytes:
    """완료 이벤트 전송"""
    payload = {"type": "complete"}
    if summary:
        payload["summary"] = summary
    return emit_bytes(payload)


def emit_phase_event(
    phase_num: int,
    phase_name: str,
    status: str,
    progress: int = 0,
    details: Optional[dict[str, Any]] = None,
) -> bytes:
    """단계 진행 이벤트 전송"""
    payload = {
        "type": "phase_event",
        "phase": phase_num,
        "name": phase_name,
        "status": status,
        "progress": progress,
    }
    if details:
        payload["details"] = details
    return emit_bytes(payload)


class PhaseTracker:
    """Phase 내 서브스텝 가중치 기반 진행률 자동 계산 + 통일된 로그 emit.

    사용 예시::

        tracker = PhaseTracker(1, "LLM 분석", total_phases=3)
        tracker.define_steps([
            ("결정론적 링킹", 5),
            ("SQL→Table 매칭", 20),
            ("Call 매칭", 20),
            ("후처리", 5),
            ("메서드 요약", 50),
        ])
        yield tracker.begin_step(0, len(items))
        for item in items:
            msg, evt = tracker.tick()
            yield msg; yield evt
        yield tracker.end_step("refer_to=42")
    """

    def __init__(self, phase_num: int, phase_name: str, total_phases: int):
        self.phase_num = phase_num
        self.phase_name = phase_name
        self.total_phases = total_phases
        self._steps: list[dict[str, Any]] = []
        self._current_step_idx = 0

    def define_steps(self, steps: list[tuple[str, int]]) -> None:
        """서브스텝 정의. steps = [(name, weight), ...] weight 합 = 100"""
        self._steps = [
            {"name": n, "weight": w, "total": 0, "done": 0}
            for n, w in steps
        ]

    def begin_step(self, step_idx: int, total_items: int) -> bytes:
        """서브스텝 시작 — 메시지 + phase_event emit 결합"""
        self._current_step_idx = step_idx
        step = self._steps[step_idx]
        step["total"] = total_items
        step["done"] = 0
        progress = self._calc_progress()
        step_label = f"[{self.phase_num + 1}/{self.total_phases}단계] {step['name']}"
        msg = f"{step_label} ({total_items}건)"
        return (
            emit_user_message(msg)
            + emit_phase_event(
                self.phase_num, self.phase_name, "in_progress", progress,
                {"step": step["name"], "total": total_items},
            )
        )

    def tick(self, count: int = 1) -> tuple[bytes, bytes]:
        """아이템 완료 — (message, phase_event) 튜플 반환. 단위마다 사용자 메시지 자동.

        호출자 패턴:
            msg, evt = tracker.tick(count)
            yield msg
            yield evt
        """
        step = self._steps[self._current_step_idx]
        step["done"] = min(step["done"] + count, step["total"])
        progress = self._calc_progress()
        msg = emit_user_message(
            f"  ├─ [{step['done']}/{step['total']}] {step['name']} 처리 중..."
        )
        evt = emit_phase_event(
            self.phase_num, self.phase_name, "in_progress", progress,
            {"step": step["name"], "done": step["done"], "total": step["total"]},
        )
        return (msg, evt)

    def end_step(self, metrics: str = "") -> bytes:
        """서브스텝 완료 요약 + phase_event 진행률 갱신"""
        step = self._steps[self._current_step_idx]
        step["done"] = step["total"]  # 완료로 마킹
        progress = self._calc_progress()
        suffix = f" | {metrics}" if metrics else ""
        return (
            emit_user_message(f"  └─ {step['name']} 완료{suffix}")
            + emit_phase_event(
                self.phase_num, self.phase_name, "in_progress", progress,
                {"step": step["name"], "done": step["total"], "total": step["total"]},
            )
        )

    def _calc_progress(self) -> int:
        """전체 가중치 기반 진행률 계산 (0~100)"""
        total_progress = 0
        for i, step in enumerate(self._steps):
            if i < self._current_step_idx:
                total_progress += step["weight"]
            elif i == self._current_step_idx and step["total"] > 0:
                total_progress += int(step["done"] / step["total"] * step["weight"])
        return min(total_progress, 100)


async def stream_with_error_boundary(
    async_gen: AsyncGenerator[bytes, None],
) -> AsyncGenerator[bytes, None]:
    """스트리밍 처리 경계"""
    trace_id = f"stream-{uuid.uuid4().hex[:8]}"

    try:
        async for chunk in async_gen:
            yield chunk
        yield emit_complete()
    except GeneratorExit:  # noqa: SILENT_FAIL — 클라이언트 정상 종료 시그널, raise 하면 stream 정리 깨짐
        logging.info(f"[{trace_id}] 클라이언트 연결 종료")
    except Exception as e:  # noqa: SILENT_FAIL — 스트림 경계 안전망. error 를 NDJSON 으로 emit 하므로 클라이언트가 결과 인지 가능
        error_msg = f"{e.__class__.__name__}: {str(e)}"
        logging.error(f"[{trace_id}] 스트림 에러: {error_msg}", exc_info=True)
        yield emit_error(error_msg, error_type=e.__class__.__name__, trace_id=trace_id)


# ═══════════════════════════════════════════════════════════════════════
# UI 메시지 헬퍼 — 사용자 콘솔용 자연어
# ═══════════════════════════════════════════════════════════════════════

def emit_phase_header(phase_num, title: str, detail: str = "") -> bytes:
    """단계 헤더. 예: [1단계] AST 구조 생성 (10개 파일)"""
    phase_title = f"[{phase_num}단계] " if phase_num else ""
    return emit_user_message(f"{phase_title}{title}" + (f" ({detail})" if detail else ""))


def emit_separator() -> bytes:
    """구분선."""
    return emit_user_message("━" * 50)


def emit_file_start(file_idx: int, total: int, file_name: str) -> bytes:
    """파일 처리 시작. 예: 📄 [1/10] OrderService.java"""
    return emit_user_message(f"📄 [{file_idx}/{total}] {file_name}")


def emit_phase_complete(phase_num, summary: str) -> bytes:
    """단계 완료. 예: ✅ 1단계 완료: 구조 노드 25개"""
    return emit_user_message(f"   ✅ {phase_num}단계 완료: {summary}")


def emit_skip(reason: str) -> bytes:
    """건너뛰기. 예: ℹ️ DDL 없음"""
    return emit_user_message(f"ℹ️ {reason}")


# ═══════════════════════════════════════════════════════════════════════
# LLM 호출 표준 — 직전·직후 emit 자연 yield (어댑터 X, 표준 인터페이스)
# ═══════════════════════════════════════════════════════════════════════

async def stream_llm_call(
    operation_name: str,
    fn: Callable,
    *args,
    **kwargs,
) -> AsyncGenerator[bytes, None]:
    """LLM 호출 표준 — 동기 함수를 별도 스레드 실행 + 직전·직후 사용자 메시지.

    호출자 패턴 (분석기 흐름에 자연 흡수):
        result = None
        async for evt in stream_llm_call("테이블 보강", prompts.table_summary, ...):
            if isinstance(evt, bytes):
                yield evt
            else:
                result = evt

    마지막 yield 가 result (bytes 가 아닌 LLM 응답 객체).
    """
    yield emit_user_message(f"  ├─ {operation_name} 분석 중...")
    result = await asyncio.to_thread(fn, *args, **kwargs)
    yield emit_user_message(f"  ├─ {operation_name} 완료")
    yield result  # 마지막에 결과 객체 (호출자가 isinstance 로 분기)


