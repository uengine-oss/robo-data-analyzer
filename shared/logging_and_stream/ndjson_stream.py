"""NDJSON 단일 스트림 emit — 폴링 대체 + 일시정지/재개 흡수 (Plan §4 M1).

``/robo/pipeline/status`` 폴링 폐기. ``/robo/pipeline/control`` (pause/resume) 폐기 —
LangGraph Checkpointer 가 흡수 (클라이언트가 stream 끊으면 자동 일시정지, 같은
``thread_id`` 로 재호출 시 자동 resume).

분석 시작 시 long-lived NDJSON connection 으로 모든 이벤트 push:

- ``phase_event``  — step 시작 / 완료 / 종료
- ``tool_event``   — Agent 도구 호출 친화 메시지
- ``todo_update``  — Agent todo 진행
- ``error``        — 오류 (silent fail 금지 — 명시 emit)
- ``warning``      — 경고
- ``stream_end``   — 스트림 종료 신호

★ logger mirror: ``emit()`` 가 동시에 ``logger.info`` 호출 — 모든 NDJSON 이벤트가
``analyzer.log`` 에 자동 진입. (트렁크 녹임 — 각 호출처에서 따로 log_info 안 부름)
"""
from __future__ import annotations

import asyncio
import json
from typing import AsyncIterator

from shared.config.env_catalog import ENV
from shared.logging_and_stream.logger import log_info, log_warning


# 이벤트 type 별 logger component 이름 (analyzer.log 에 표시).
# stream_end 는 close 신호일 뿐 mirror 안 함 (노이즈).
_EVENT_LOGGER: dict[str, str] = {
    "phase_event": "stream.phase",
    "tool_event":  "stream.tool",
    "todo_update": "stream.todo",
    "error":       "stream.error",   # log_error 도 별도 호출됐을 가능성 — mirror 는 INFO 로
    "warning":     "stream.warning",
}


def _shorten_path(p: str) -> str:
    """긴 path → 짧은 표시 — ``ROBO_DATA_DIR/source/<project>/`` 이하 상대 경로만.

    예: ``C:\\uEngine\\robo\\data\\source\\apascrt00010t02_20260406\\zapamcom10060.c``
    → ``apascrt00010t02_20260406\\zapamcom10060.c`` (project_root 의 마지막 segment 부터).
    """
    if not isinstance(p, str) or not p:
        return str(p)
    try:
        data_dir = str(ENV.data.dir).replace("\\", "/").rstrip("/")
    except Exception:
        data_dir = ""
    norm = p.replace("\\", "/")
    # ROBO_DATA_DIR 안이면 그 이후만
    for marker in (f"{data_dir}/source/", f"{data_dir}/ddl/", "/data/source/", "/data/ddl/"):
        if marker and marker in norm:
            return norm.split(marker, 1)[1]
    # 그 외 — 마지막 2 segment 만 (디렉토리/파일)
    parts = norm.split("/")
    if len(parts) > 2:
        return "/".join(parts[-2:])
    return norm


def _shorten_args(tool_name: str, args: dict) -> dict:
    """도구별 핵심 인자만 추출 — path 단축, 긴 pattern 줄임."""
    if not isinstance(args, dict):
        return {}
    out: dict = {}
    for key in ("path", "file", "base"):
        v = args.get(key)
        if v:
            out[key] = _shorten_path(v)
    pat = args.get("pattern")
    if pat:
        out["pattern"] = pat if len(str(pat)) <= 40 else str(pat)[:37] + "..."
    glob_pat = args.get("path_glob")
    if glob_pat and glob_pat != "**/*":
        out["glob"] = glob_pat
    for key in ("anchor", "start", "end", "max_lines", "name"):
        v = args.get(key)
        if v is not None:
            out[key] = v
    return out


def _mirror_tool_event(body: dict) -> None:
    """tool_event 전용 — 도구명을 op 라벨로, args 단축, 결과 통계만.

    예 (이전):
      🔧 도구 │ ▶ 도구: open|close|insert|... 정규식 검색 중... Agent=profile 도구=grep args={...풀...}
    예 (이후):
      🔧 도구 │ ▶ grep: pattern=open|close|insert|... base=apascrt00010t02_20260406
      🔧 도구 │ ✓ grep: 매치=13 소요ms=0
    """
    sub_event = body.get("event")
    tool_name = body.get("tool") or "?"
    elapsed_ms = body.get("elapsed_ms")
    error = body.get("error")
    short_args = _shorten_args(tool_name, body.get("args") or {})

    if sub_event == "tool_start":
        op_label = f"▶ {tool_name}"
        log_info("stream.tool", op_label, **short_args)
        return
    if sub_event == "tool_error":
        op_label = f"✗ {tool_name}"
        log_info("stream.tool", op_label, error=error, elapsed_ms=elapsed_ms)
        return
    # tool_end — 결과 통계 + 소요만 (인자는 시작 라인에 이미 있음).
    op_label = f"✓ {tool_name}"
    result_keys = {"항목", "DIR", "FILE", "파일", "매치", "읽은줄", "함수", "SKILL", "줄수", "결과"}
    stats = {k: v for k, v in body.items() if k in result_keys}
    log_info("stream.tool", op_label, elapsed_ms=elapsed_ms, **stats)


def _mirror_event_to_logger(event: dict) -> None:
    """NDJSON 이벤트 1 건을 ``analyzer.log`` 에 한 줄로 mirror.

    노이즈 방지:
    - ``stream_end`` 는 mirror X (close 신호일 뿐).
    - 빈 값 / None 키는 ``log_info`` 의 ``_format_kv`` 가 자동 생략.
    - event 의 ``type`` 키는 component 결정에 쓰고 본문에선 제거 (중복 방지).
    - ``todos`` (todo_update) 는 full list 박지 X → in_progress 1 개 + counts 만 추출.
    - ``tool_event`` 는 전용 분기 — 도구명을 op 라벨로, args 단축.
    """
    etype = event.get("type")
    component = _EVENT_LOGGER.get(etype)
    if component is None:
        return

    # event 본문에서 type 빼고 모두 context 로 전달 (operation 결정용 키도 추출).
    body = {k: v for k, v in event.items() if k != "type"}

    # tool_event 는 전용 처리 — 도구명 op 라벨 / args 단축.
    if etype == "tool_event":
        _mirror_tool_event(body)
        return

    operation = body.pop("event", None) or body.pop("operation", None) or etype

    # todo_update 의 todos full list → 요약 (in_progress / counts).
    todos = body.pop("todos", None)
    if isinstance(todos, list) and todos:
        in_progress = next(
            (t.get("content") for t in todos
             if isinstance(t, dict) and t.get("status") == "in_progress"),
            None,
        )
        done = sum(1 for t in todos if isinstance(t, dict) and t.get("status") == "completed")
        body["in_progress"] = in_progress
        body["done"] = done
        body["total"] = len(todos)

    log_info(component, str(operation), **body)


class NDJSONStream:
    """파이프라인 → 프론트 NDJSON 스트림.

    Agent 미들웨어가 ``emit(event)`` 호출 → ``iter()`` 가 한 줄씩 yield.
    queue 는 unbounded (size 미지정) — 백프레셔는 클라이언트가 stream 끊으면
    Checkpointer 가 흡수.
    """

    def __init__(self):
        self._queue: asyncio.Queue = asyncio.Queue()
        self._closed = False

    def emit(self, event: dict) -> None:
        """이벤트 1개 emit — 미들웨어가 호출 (sync from async context 안전).

        ★ logger mirror — 큐에 넣기 직전에 ``analyzer.log`` 에도 한 줄 push.
        프론트 클라이언트 연결 없어도 (CLI 단독 실행) 파일에 흔적 남음.
        """
        if self._closed:
            log_warning(
                "logging_and_stream.ndjson_stream",
                "emit_after_close",
                "스트림 종료 후 emit — 이벤트 drop",
                event_type=event.get("type"),
            )
            return

        # logger mirror 먼저 — emit 실패해도 로그는 남게 (silent fail X)
        _mirror_event_to_logger(event)

        try:
            self._queue.put_nowait(event)
        except asyncio.QueueFull:
            log_warning(
                "logging_and_stream.ndjson_stream",
                "queue_full",
                "NDJSON queue 가 가득참 (unbounded 라 도달 안 해야 정상) — drop",
                event_type=event.get("type"),
            )

    async def close(self) -> None:
        self._closed = True
        await self._queue.put({"type": "stream_end"})

    async def iter(self) -> AsyncIterator[bytes]:
        """NDJSON 라인 비동기 iterator — FastAPI StreamingResponse 가 소비."""
        while True:
            event = await self._queue.get()
            line = json.dumps(event, ensure_ascii=False) + "\n"
            yield line.encode("utf-8")
            if event.get("type") == "stream_end":
                break
