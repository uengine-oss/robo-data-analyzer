"""분석 run 일시정지 / 재개 / 중단 컨트롤러 — 같은 세션(프로세스) 안의 hold.

control 엔드포인트(다른 asyncio task)가 신호를 set 하고, run 태스크가 안전 지점에서
``gate()`` 로 소비한다. 영속(디스크) 재개가 아니라 **프로세스가 살아있는 동안의 hold** —
서버를 끄면 run 도 끝난다 (소프트 범위: 화면을 유지하는 동안만 유효).

일시정지 순간 진행 중인 LLM 함수 분석 task 는 ``request_pause`` 가 **즉시 cancel** 한다.
재개하면 호출자가 미완료분을 재투입 — 함수 분석은 Neo4j MERGE/SET 이라 재실행이 안전.

게이트는 단 두 곳:
- step 경계 (executor 의 astream 소비 루프) — 모든 step 공통 hold 지점.
- step6 라운드 루프 — 멀티-아워 무거운 경로의 즉시-취소 hold 지점.
"""
from __future__ import annotations

import asyncio
from typing import Optional

from log.logger import log_info
from log.ndjson_stream import NDJSONStream


class RunStopped(Exception):
    """사용자가 중단(stop)을 요청 — run 을 정상 종료로 끝내는 sentinel (실패가 아님)."""


class PauseController:
    """단일 진실 — pause/resume/stop 신호의 set(엔드포인트) 와 소비(run 태스크)를 중개."""

    def __init__(self, stream: Optional[NDJSONStream] = None) -> None:
        self._stream = stream
        self._resumed = asyncio.Event()
        self._resumed.set()                      # 시작 상태 = not-paused (gate 통과)
        self._paused = False
        self._stopped = False
        self._inflight: list[asyncio.Task] = []   # 취소 대상 in-flight task (라운드마다 갱신)

    # ── 엔드포인트가 부르는 신호 (set) ─────────────────────────────
    def request_pause(self) -> None:
        if self._stopped or self._paused:
            return
        self._paused = True
        self._resumed.clear()
        self._cancel_inflight()
        log_info("run.control", "pause_requested")

    def resume(self) -> None:
        if self._stopped or not self._paused:
            return
        self._paused = False
        self._resumed.set()                      # gate 에서 대기 중인 run 태스크 깨움
        log_info("run.control", "resume_requested")

    def request_stop(self) -> None:
        if self._stopped:
            return
        self._stopped = True
        self._paused = False
        self._resumed.set()                      # gate 가 깨어나 RunStopped raise
        self._cancel_inflight()
        log_info("run.control", "stop_requested")

    def status(self) -> dict:
        """프론트 응답 형태 그대로 — ``{"isPaused": ..., "isStopped": ...}``."""
        return {"isPaused": self._paused, "isStopped": self._stopped}

    # ── run 태스크가 부르는 소비 ───────────────────────────────────
    def set_inflight(self, tasks: list[asyncio.Task]) -> None:
        """현재 라운드의 취소 대상 task 등록 — 엔드포인트가 즉시 cancel 할 수 있도록."""
        self._inflight = list(tasks)

    def clear_inflight(self) -> None:
        self._inflight = []

    async def gate(self) -> None:
        """안전 지점에서 호출 — stopped 면 ``RunStopped`` raise, paused 면 resume 까지 hold.

        paused 진입/해제 시 NDJSON ``phase_event`` 한 줄(paused/resumed) emit.
        """
        if self._stopped:
            raise RunStopped()
        if self._paused:
            self._emit("paused")
            await self._resumed.wait()
            if self._stopped:
                raise RunStopped()
            self._emit("resumed")

    # ── 내부 ───────────────────────────────────────────────────────
    def _cancel_inflight(self) -> None:
        for task in self._inflight:
            if not task.done():
                task.cancel()
        self._inflight = []

    def _emit(self, event: str) -> None:
        if self._stream is not None:
            self._stream.emit({"type": "phase_event", "event": event})


# control 신호가 없는 경로(CLI 단독 실행·테스트) 용 no-op — 절대 paused/stopped 되지
# 않으므로 gate() 는 즉시 통과, set_inflight 는 무해. step6 가 cfg 에 pause 가 없을 때 사용.
NULL_PAUSE = PauseController()
