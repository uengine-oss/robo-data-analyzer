"""NDJSON 단일 스트림 emit — 진행 상태 단일 채널 (폴링 없음).

진행 상태는 폴링이 아니라 이 NDJSON 스트림으로 push. 일시정지/재개/중단 제어는 별도
``POST /robo/analyze/control`` 엔드포인트가 담당 — 그 상태 전이(paused/resumed/stopped)도
여기 ``phase_event`` 로 흘러나온다.

분석 시작 시 long-lived NDJSON connection 으로 모든 이벤트 push:

- ``phase_event``         — step 시작 / 완료 / 종료
- ``tool_event``          — Agent 도구 호출 친화 메시지
- ``processing_event``    — step 내부 처리 단위 진행
- ``message``             — 단계별 사용자 친화 완료 메시지
- ``progress`` / ``result`` — 검색 진행과 최종 결과
- ``node_event``          — 그래프 노드 생성/갱신 (label/id/props)
- ``relationship_event``  — 그래프 엣지 생성/갱신 (rel_type/from/to)
- ``error``               — 오류 (silent fail 금지 — 명시 emit)
- ``warning``             — 경고
- ``stream_end``          — 스트림 종료 신호

★ logger mirror: ``emit()`` 가 동시에 ``logger.info`` 호출 — 모든 NDJSON 이벤트가
``analyzer.log`` 에 자동 진입. (트렁크 녹임 — 각 호출처에서 따로 log_info 안 부름)
"""
from __future__ import annotations

import asyncio
import json
from typing import AsyncIterator

from shared.config.settings import SETTINGS
from shared.events.runtime_logger import log_info, log_warning


# 합성 그래프 이벤트 실측: Python retained memory 약 983 bytes/event. 1,024건은 약 1MiB이며,
# 현재 14,128건 무소비 burst(13.21MiB)를 13분의 1 수준으로 제한한다. 물리 queue를 bounded로
# 만들면 동기 제어/로그 emit이 QueueFull에서 유실되므로, await 가능한 대량 생산자만 이 경계에서
# drain을 기다린다(spec 062 T019).
ASYNC_EVENT_HIGH_WATER_MARK = 1024


# 이벤트 type 별 logger component 이름 (analyzer.log 에 표시).
# stream_end 는 close 신호일 뿐 mirror 안 함 (노이즈).
_EVENT_LOGGER: dict[str, str] = {
    "phase_event":         "stream.phase",
    "tool_event":          "stream.tool",
    "processing_event":    "stream.processing",    # step 안 처리 단위 진행 (코드노드/모듈/테이블/split)
    "progress":            "stream.search",         # 검색 진행 (rewrite/recall/rerank/expand/judge)
    "result":              "stream.search",         # 검색 결과 뭉치
    "message":             "stream.message",        # 단계별 사용자 친화 완료 메시지
    "node_event":          "stream.node",          # 그래프 노드 생성/갱신
    "relationship_event":  "stream.relationship",  # 그래프 엣지 생성/갱신
    "error":               "stream.error",   # log_error 도 별도 호출됐을 가능성 — mirror 는 INFO 로
    "warning":             "stream.warning",
}


def _shorten_path(p: str) -> str:
    """긴 path → 짧은 표시 — ``ROBO_DATA_DIR/source/<project>/`` 이하 상대 경로만.

    예: ``C:\\uEngine\\robo\\data\\source\\<project_20260406>\\<module>.c``
    → ``<project_20260406>\\<module>.c`` (project_root 의 마지막 segment 부터).
    """
    if not isinstance(p, str) or not p:
        return str(p)
    data_dir = str(SETTINGS.analysis_data.dir).replace("\\", "/").rstrip("/")
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


# 도구별 한글 라벨 — start/done/error 의 메시지 prefix 공통.
# 새 도구 추가 시 이 dict 한 줄. 매핑 없으면 영어 도구명 그대로 (graceful fallback).
_TOOL_LABELS: dict[str, str] = {
    "ls":                   "디렉토리 조회",
    "glob_search":          "파일 검색",
    "grep":                 "검색",
    "read_file":            "파일 읽기",
    "read_file_range":      "파일 범위 읽기",
    "list_code_nodes":       "코드노드목록 조회",
    "list_skills":          "SKILL 목록 조회",
    "read_skill":           "SKILL 읽기",
    "verify_table_regex":   "정규식 검증",
}

# 도구별 핵심 인자 — start ↔ done 양쪽에 박힘 (대상 식별 / 대칭).
# 도구의 args dict 에서 이 키들만 추출. path 류는 _shorten_path 자동 적용.
# 키 자체는 영어 그대로 — logger 의 _KEY_LABELS 가 출력 시 한글화.
_TOOL_PRIMARY_ARGS: dict[str, tuple[str, ...]] = {
    "ls":                   ("path",),
    "glob_search":          ("pattern", "base"),
    "grep":                 ("pattern", "base", "path_glob"),
    "read_file":            ("path", "file", "max_lines"),
    "read_file_range":      ("path", "file", "anchor", "start", "end"),
    "list_code_nodes":       ("path", "file"),
    "list_skills":          (),
    "read_skill":           ("name",),
    "verify_table_regex":   ("call_regex",),
}

# path / base / file 류 — 자동 단축 적용 (project_root 이하 상대 경로).
_PATH_LIKE_KEYS = frozenset({"path", "file", "base"})


def _primary_args(tool_name: str, args: dict) -> dict:
    """도구별 핵심 인자 추출 + path 단축. start/done 양쪽에서 사용 (대칭)."""
    if not isinstance(args, dict):
        return {}
    out: dict = {}
    for key in _TOOL_PRIMARY_ARGS.get(tool_name, ()):
        v = args.get(key)
        if v is None or v == "":
            continue
        if key in _PATH_LIKE_KEYS and isinstance(v, str):
            v = _shorten_path(v)
        # pattern 잘림 X — 검색 의도가 핵심 정보. 200자 한도 (이상은 잘림).
        if key == "pattern" and isinstance(v, str) and len(v) > 200:
            v = v[:197] + "..."
        out[key] = v
    return out


def _mirror_tool_event(body: dict) -> None:
    """tool_event 전용 — 한글 도구 라벨 + start ↔ done 대상 식별 대칭.

    예::

      🔧 도구 │ ▶ 검색 │ 패턴: pfmdbio|DBIO|EXEC SQL · 경로: apascrt00010t02 · 글롭: **/*.c
      🔧 도구 │ ✓ 검색 완료 │ 패턴: pfmdbio|DBIO|EXEC SQL · 경로: apascrt00010t02 · 매치: 80 · 소요: 15ms
      🔧 도구 │ ✗ 검색 실패 │ 에러: ... · 소요: 15ms
    """
    sub_event = body.get("event")
    tool_name = body.get("tool") or "?"
    elapsed_ms = body.get("elapsed_ms")
    error = body.get("error")
    args = _primary_args(tool_name, body.get("args") or {})
    label = _TOOL_LABELS.get(tool_name, tool_name)

    if sub_event == "tool_start":
        log_info("stream.tool", f"▶ {label}", **args)
        return
    if sub_event == "tool_error":
        log_info("stream.tool", f"✗ {label} 실패", error=error, elapsed_ms=elapsed_ms, **args)
        return
    # tool_end — 핵심 인자 (대상 식별) + 결과 통계 + 소요. start 와 대칭.
    result_keys = {"항목", "DIR", "FILE", "파일", "매치", "읽은줄", "함수", "SKILL", "줄수", "결과"}
    stats = {k: v for k, v in body.items() if k in result_keys}
    log_info("stream.tool", f"✓ {label} 완료", **args, **stats, elapsed_ms=elapsed_ms)


def _mirror_event_to_logger(event: dict) -> None:
    """NDJSON 이벤트 1 건을 ``analyzer.log`` 에 한 줄로 mirror.

    노이즈 방지:
    - ``stream_end`` 는 mirror X (close 신호일 뿐).
    - 빈 값 / None 키는 ``log_info`` 의 ``_format_kv`` 가 자동 생략.
    - event 의 ``type`` 키는 component 결정에 쓰고 본문에선 제거 (중복 방지).
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
    origin_component = body.pop("component", None)
    if origin_component:
        # ``log_info(component, ...)``의 위치 인자와 wire payload의 원발생 component가
        # 충돌하지 않게 이름을 분리한다. error/warning의 출처 정보는 그대로 보존한다.
        body["origin_component"] = origin_component

    log_info(component, str(operation), **body)


class NDJSONStream:
    """파이프라인 → 프론트 NDJSON 스트림.

    Agent 미들웨어가 ``emit(event)`` 호출 → ``iter()`` 가 한 줄씩 yield.
    물리 queue는 동기 제어/로그 이벤트의 QueueFull 유실을 막기 위해 lossless다. 대량 그래프
    생산자는 ``emit_async``를 사용하며 1,024건 high-water에서 소비자 drain을 기다린다.
    """

    def __init__(self):
        self._queue: asyncio.Queue = asyncio.Queue()
        self._closed = False
        self._capacity_available = asyncio.Event()
        self._capacity_available.set()
        self._peak_queued_event_count = 0
        self._consumer_detached = False

    @property
    def queued_event_count(self) -> int:
        return self._queue.qsize()

    @property
    def peak_queued_event_count(self) -> int:
        return self._peak_queued_event_count

    def _prepare_event(self, event: dict) -> bool:
        """닫힘 검사와 logger mirror를 동기/비동기 발신 경로에서 한 번만 정의한다."""
        if self._closed:
            log_warning(
                "shared.events.ndjson_event_stream",
                "emit_after_close",
                "스트림 종료 후 emit — 이벤트 drop",
                event_type=event.get("type"),
            )
            return False
        _mirror_event_to_logger(event)
        return True

    def _enqueue(self, event: dict) -> None:
        self._queue.put_nowait(event)
        self._peak_queued_event_count = max(
            self._peak_queued_event_count,
            self._queue.qsize(),
        )

    def emit(self, event: dict) -> None:
        """이벤트 1개 emit — 미들웨어가 호출 (sync from async context 안전).

        ★ logger mirror — 큐에 넣기 직전에 ``analyzer.log`` 에도 한 줄 push.
        프론트 클라이언트 연결 없어도 (CLI 단독 실행) 파일에 흔적 남음.
        """
        if not self._prepare_event(event):
            return
        self._enqueue(event)

    async def emit_async(self, event: dict) -> None:
        """대량 이벤트 무손실 발신 — high-water 이상이면 소비자 drain까지 생산자만 대기한다."""
        if not self._prepare_event(event):
            return
        while self._queue.qsize() >= ASYNC_EVENT_HIGH_WATER_MARK:
            self._capacity_available.clear()
            # get과 clear 사이 race에서 이미 drain됐으면 기다리지 않는다.
            if self._queue.qsize() < ASYNC_EVENT_HIGH_WATER_MARK:
                self._capacity_available.set()
                break
            await self._capacity_available.wait()
        self._enqueue(event)

    async def close(self) -> None:
        self._closed = True
        if self._consumer_detached:
            # 응답 소비자가 사라진 뒤에는 stream_end를 받을 주체가 없다. 가득 찬 queue의 drain을
            # 기다리면 producer cancellation의 finally가 영원히 끝나지 않는다.
            return
        while self._queue.qsize() >= ASYNC_EVENT_HIGH_WATER_MARK:
            self._capacity_available.clear()
            if self._queue.qsize() < ASYNC_EVENT_HIGH_WATER_MARK:
                self._capacity_available.set()
                break
            await self._capacity_available.wait()
        self._enqueue({"type": "stream_end"})

    async def iter(self) -> AsyncIterator[bytes]:
        """NDJSON 라인 비동기 iterator — FastAPI StreamingResponse 가 소비."""
        while True:
            event = await self._queue.get()
            if self._queue.qsize() < ASYNC_EVENT_HIGH_WATER_MARK:
                self._capacity_available.set()
            line = json.dumps(event, ensure_ascii=False) + "\n"
            yield line.encode("utf-8")
            if event.get("type") == "stream_end":
                break

    async def iter_with_producer(
        self,
        producer: asyncio.Task[None],
    ) -> AsyncIterator[bytes]:
        """응답 소비자와 producer task를 하나의 수명주기로 묶는다.

        정상 ``stream_end`` 뒤에는 producer 종료를 await해 task 예외를 수거한다. 클라이언트
        연결 종료로 iterator가 먼저 닫히면 producer를 취소하고 ``finally`` 정리까지 수거한다.
        라우터가 ``create_task`` 참조를 버려 요청 밖에서 비용·DB 작업이 계속되는 것을 막는다.
        """
        reached_stream_end = False
        cancelled_for_consumer = False
        try:
            async for chunk in self.iter():
                yield chunk
            reached_stream_end = True
        finally:
            if not reached_stream_end and not producer.done():
                cancelled_for_consumer = True
                self._consumer_detached = True
                self._capacity_available.set()
                producer.cancel()
            try:
                await producer
            except asyncio.CancelledError:
                if not cancelled_for_consumer:
                    raise
