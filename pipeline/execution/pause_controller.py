"""분석 run 일시정지 / 재개 / 중단 컨트롤러 — 같은 세션(프로세스) 안의 hold.

control 엔드포인트(다른 asyncio task)가 신호를 set 하고, run 태스크가 안전 지점에서
``gate()`` 로 소비한다. 영속(디스크) 재개가 아니라 **프로세스가 살아있는 동안의 hold** —
서버를 끄면 run 도 끝난다 (소프트 범위: 화면을 유지하는 동안만 유효).

일시정지 순간 진행 중인 LLM 함수 분석 task 는 ``request_pause`` 가 **즉시 cancel** 한다.
재개하면 호출자가 미완료분을 재투입 — 함수 분석은 Neo4j MERGE/SET 이라 재실행이 안전.

게이트 위치:
- step 경계 (executor 의 astream 소비 루프) — 모든 step 공통 hold 지점.
- step2 파일·연결·저장·스트림 배치 경계 — AST 적재 중 cooperative hold 지점.
- step6/7 라운드·fan-out — 멀티-아워 무거운 경로의 즉시-취소 hold 지점.
"""
from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable, Optional, TypeVar

from shared.events.runtime_logger import log_info
from shared.events.ndjson_event_stream import NDJSONStream


T = TypeVar("T")


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
        self._controller_cancelled_tasks: set[asyncio.Task] = set()

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

    async def cancel_and_wait(self, tasks: list[asyncio.Task[Any]]) -> list[Any]:
        """등록 해제 후 미완료 task를 취소하고 모든 종료·예외를 수거한다.

        pause/stop이 이미 ``_cancel_inflight``를 호출했어도 로컬 task 목록으로 await하므로
        coroutine ``finally``가 끝나기 전에 재투입이나 run 종료로 넘어가지 않는다.
        """
        self.clear_inflight()
        for task in tasks:
            if not task.done():
                task.cancel()
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for task in tasks:
            self._controller_cancelled_tasks.discard(task)
        return results

    async def run_restartable_during_pause(
        self,
        operation: Callable[[], Awaitable[T]],
    ) -> T:
        """pause/stop이 취소한 단일 작업만 resume 후 재시도한다.

        상위 task·클라이언트 연결 종료가 일으킨 외부 cancellation은 재시도로 위장하지 않고
        그대로 전파한다. 각 시도는 cancellation ``finally``까지 수거한다.
        """
        while True:
            await self.gate()
            task = asyncio.create_task(operation())
            self.set_inflight([task])
            controller_cancelled = False
            try:
                return await task
            except asyncio.CancelledError:
                controller_cancelled = task in self._controller_cancelled_tasks
                if not controller_cancelled:
                    raise
            finally:
                await self.cancel_and_wait([task])
            # pause면 gate에서 resume를 기다리고, stop이면 RunStopped를 발생시킨다.
            if controller_cancelled:
                continue

    async def gate(self) -> None:
        """안전 지점에서 호출 — stopped 면 ``RunStopped`` raise, paused 면 resume 까지 hold.

        paused 진입/해제 시 NDJSON ``phase_event`` 한 줄(paused/resumed) emit.
        """
        # 상태 확인 전에 반드시 event loop에 제어권을 넘긴다. 호출자가 동기 CPU/파일
        # 배치 사이에서 gate를 호출해도 control HTTP task가 pause/stop을 먼저 기록할
        # 기회를 얻어야 이 메서드가 실제 cooperative checkpoint가 된다.
        await asyncio.sleep(0)
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
                self._controller_cancelled_tasks.add(task)
                task.cancel()
        self._inflight = []

    def _emit(self, event: str) -> None:
        if self._stream is not None:
            self._stream.emit({"type": "phase_event", "event": event})


# control 신호가 없는 경로(CLI 단독 실행·테스트) 용 no-op — 절대 paused/stopped 되지
# 않으므로 gate()는 즉시 통과, set_inflight는 무해. step7이 cfg에 pause가 없을 때 사용.
NULL_PAUSE = PauseController()
