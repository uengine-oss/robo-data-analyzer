"""NDJSON 스트리밍 유틸리티

NDJSON 스트리밍 이벤트 생성 및 처리 함수들.
기존 stream_event.py를 리네이밍.

이벤트 타입:
- message: 사용자 메시지
- data: 그래프 데이터
- error: 오류
- node_event: 노드 생성/수정 이벤트
- relationship_event: 관계 생성/수정 이벤트
- phase_event: 단계 진행 이벤트
- complete: 완료

메시지 설계 원칙 (pdb.md):
- 이 메시지는 개발자 로그가 아니라, 사용자에게 보여지는 진행 설명이다
- 조건 분기 결과가 명확히 드러날 것
- 실패/스킵/대체 상황이 숨겨지지 않을 것
- "무슨 일이 일어났는지"를 자연어로 설명할 것
"""

import json
import uuid
import logging
from typing import Any, Optional, AsyncGenerator


def emit_bytes(payload: dict) -> bytes:
    """NDJSON 형식으로 바이트 생성"""
    return json.dumps(payload, default=str, ensure_ascii=False).encode("utf-8") + b"\n"


def emit_message(content: str) -> bytes:
    """메시지 이벤트 전송"""
    return emit_bytes({"type": "message", "content": content})


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


def emit_node_event(
    action: str,
    node_type: str,
    node_name: str,
    details: Optional[dict[str, Any]] = None,
) -> bytes:
    """노드 생성/수정 이벤트 전송"""
    payload = {
        "type": "node_event",
        "action": action,
        "nodeType": node_type,
        "nodeName": node_name,
    }
    if details:
        payload["details"] = details
    return emit_bytes(payload)


def emit_relationship_event(
    action: str,
    rel_type: str,
    source: str,
    target: str,
    details: Optional[dict[str, Any]] = None,
) -> bytes:
    """관계 생성/수정 이벤트 전송"""
    payload = {
        "type": "relationship_event",
        "action": action,
        "relType": rel_type,
        "source": source,
        "target": target,
    }
    if details:
        payload["details"] = details
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
            emit_message(msg)
            + emit_phase_event(
                self.phase_num, self.phase_name, "in_progress", progress,
                {"step": step["name"], "total": total_items},
            )
        )

    def tick(self, count: int = 1) -> tuple[bytes, bytes]:
        """아이템 완료 — (message, phase_event) 튜플 반환. 메인 스레드에서만 호출."""
        step = self._steps[self._current_step_idx]
        step["done"] = min(step["done"] + count, step["total"])
        progress = self._calc_progress()
        msg = emit_message(
            f"  ├─ [{step['done']}/{step['total']}] {step['name']} 처리 중..."
        )
        evt = emit_phase_event(
            self.phase_num, self.phase_name, "in_progress", progress,
            {"step": step["name"], "done": step["done"], "total": step["total"]},
        )
        return (msg, evt)

    def tick_quiet(self, count: int = 1) -> bytes:
        """아이템 완료 — phase_event만 반환 (메시지 없이 진행률만 업데이트)."""
        step = self._steps[self._current_step_idx]
        step["done"] = min(step["done"] + count, step["total"])
        progress = self._calc_progress()
        return emit_phase_event(
            self.phase_num, self.phase_name, "in_progress", progress,
            {"step": step["name"], "done": step["done"], "total": step["total"]},
        )

    def end_step(self, metrics: str = "") -> bytes:
        """서브스텝 완료 요약 + phase_event 진행률 갱신"""
        step = self._steps[self._current_step_idx]
        step["done"] = step["total"]  # 완료로 마킹
        progress = self._calc_progress()
        suffix = f" | {metrics}" if metrics else ""
        return (
            emit_message(f"  └─ {step['name']} 완료{suffix}")
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


def build_error_body(
    exc: Exception,
    trace_id: Optional[str] = None,
    message: Optional[str] = None,
) -> dict:
    """비스트리밍 500 응답용 표준 에러 바디 생성"""
    return {
        "errorType": exc.__class__.__name__,
        "message": message or str(exc),
        "traceId": trace_id or f"req-{uuid.uuid4()}",
    }


async def stream_with_error_boundary(
    async_gen: AsyncGenerator[bytes, None],
) -> AsyncGenerator[bytes, None]:
    """스트리밍 처리 경계"""
    trace_id = f"stream-{uuid.uuid4().hex[:8]}"

    try:
        async for chunk in async_gen:
            yield chunk
        yield emit_complete()
    except GeneratorExit:
        logging.info(f"[{trace_id}] 클라이언트 연결 종료")
    except Exception as e:
        error_msg = f"{e.__class__.__name__}: {str(e)}"
        logging.error(f"[{trace_id}] 스트림 에러: {error_msg}", exc_info=True)
        yield emit_error(error_msg, error_type=e.__class__.__name__, trace_id=trace_id)
