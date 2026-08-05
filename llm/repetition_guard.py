"""생성 중 반복 폭주 실시간 차단 — 스트리밍 청크의 결정론 주기성 검사 (spec 080).

배경: 약한 모델 + 탐욕 디코딩에서 "비슷한 항목의 긴 목록 + 끝점 없는 출력" 이
공백/문구 반복 루프로 폭주한다(사건 A·B·C 실측). 기존 방어는 사후(상한·품질 게이트)라
피해가 수십~백여 초. 이 모듈은 생성 **중** 반복 궤도를 감지해 초 단위로 끊는다.
서버 네이티브 차단(`no_repeat_ngram_size`)은 사내 sglang 빌드 미지원 실측(500) → 클라이언트 구현.

감지 규칙은 단 하나(결정론): 누적 출력의 접미 창 ``WINDOW`` 자가 주기 ``p ≤ MAX_PERIOD``
로 **정확히** 반복되면 폭주다. 주기 판정은 자기중첩 ``s[p:] == s[:-p]`` (⟺ s 의 주기가 p)
슬라이스 비교로 한다 — C 구현 memcmp 라 검사당 1ms 미만이고, 주기가 창 길이의 약수가
아니어도 정확하다(회전 트릭은 약수 조건이 필요해 부적합 — 단위테스트로 반증됨).
공백 폭주는 일반 주기 창을 기다리지 않는다. JSON 루트가 닫힌 뒤에는 8자, 그 밖의
문법 공백은 32자 연속에서 별도로 중단한다. 문자열 값 안의 공백은 세지 않는다.

오탐 안전 논거: 정상 구조화 출력은 항목마다 내용이 달라 3,072자를 정확히 주기 반복할 수
없다(JSON 키 반복은 사이에 서로 다른 내용이 끼어 비주기). 창보다 짧은 출력은 검사가
시작되지 않아 한줄요약 등 소형 룰은 원천 면제다.
"""
from __future__ import annotations

from typing import Any, Optional

from langchain_core.callbacks import AsyncCallbackHandler


def _tool_argument_delta(container: Any) -> str:
    """Extract streamed tool argument text from LangChain/provider chunk shapes."""
    if container is None:
        return ""
    pieces: list[str] = []
    for call in getattr(container, "tool_call_chunks", None) or []:
        args = call.get("args") if isinstance(call, dict) else getattr(call, "args", None)
        if isinstance(args, str):
            pieces.append(args)
    if pieces:
        return "".join(pieces)
    for call in (getattr(container, "additional_kwargs", None) or {}).get("tool_calls", []):
        if not isinstance(call, dict):
            continue
        args = (call.get("function") or {}).get("arguments")
        if isinstance(args, str):
            pieces.append(args)
    return "".join(pieces)


def _streamed_text(token: str, chunk: Any) -> str:
    """Return one non-duplicated content or tool-argument delta for the callback."""
    if token:
        return token
    if chunk is None:
        return ""
    text = getattr(chunk, "text", "") or ""
    if text:
        return text
    message = getattr(chunk, "message", None)
    return _tool_argument_delta(message) or _tool_argument_delta(chunk)


class RepetitionLoopAborted(RuntimeError):
    """반복 폭주 감지로 생성을 조기 중단 — 호출부 예외 경로로 흡수(부분 표시·무중단).

    ``partial_text`` 는 중단 시점까지 수신한 출력의 접미 tail(최대 ``TAIL_KEEP`` 자)이다.
    이것이 없으면 에러 기록이 response=None 이 되어 폭주 내용(반복 패턴)을 사후 진단할 수
    없다(spec 116 0단계 — raw 보존).
    """

    def __init__(
        self, label: str, period: int, chars_seen: int, partial_text: str = ""
    ) -> None:
        self.label = label
        self.period = period
        self.chars_seen = chars_seen
        self.partial_text = partial_text
        super().__init__(
            f"repetition loop aborted: {label} — period={period} chars, "
            f"chars_seen={chars_seen} (spec 080)"
        )


class RepetitionDetector:
    """접미 창 정확 주기성 검사기 — 상태는 tail 문자열 하나, 판정은 순수 결정론.

    ``feed()`` 가 감지 시 최소 주기(int)를 반환, 미감지면 None.
    """

    WINDOW = 3072        # 판정 창(자) — 이만큼 정확 반복돼야 폭주로 본다
    MAX_PERIOD = 512     # 허용 최대 주기 → 반복 횟수 W/p ≥ 6 자동 보장
    CHECK_EVERY = 128    # 검사 주기(신규 수신 자 수) — 검사 비용 억제
    TAIL_KEEP = 6144     # tail 보관 상한 (WINDOW 의 2배)
    # JSON 토큰 사이 정상 공백/개행보다 충분히 크지만, 실제 2026-07-30 사건의 512자
    # 공백 루프보다 훨씬 일찍 서버 스트림을 취소한다. 부분 JSON을 복구·채택하지 않는다.
    WHITESPACE_FLOOD = 32
    # 완성된 JSON 루트 뒤에는 정상적으로 개행 한두 자가 붙을 수 있어도 긴 공백은 필요 없다.
    # root-aware라 문자열 값/들여쓰기에 오탐하지 않고 GPU decode를 더 일찍 취소할 수 있다.
    POST_ROOT_WHITESPACE_FLOOD = 8

    def __init__(self) -> None:
        self._tail = ""
        self._pending = 0
        self.chars_seen = 0
        self._json_stack: list[str] = []
        self._json_started = False
        self._json_root_complete = False
        self._json_in_string = False
        self._json_escaped = False
        self._post_root_whitespace = 0

    @property
    def tail_text(self) -> str:
        """수신 누적의 접미 tail — 중단 예외에 실어 사후 진단용으로 보존한다."""
        return self._tail

    def feed(self, text: str) -> Optional[int]:
        if not text:
            return None
        self.chars_seen += len(text)
        self._tail = (self._tail + text)[-self.TAIL_KEEP:]
        self._pending += len(text)
        if self._track_json_boundary(text):
            return 1
        trailing_whitespace = len(self._tail) - len(self._tail.rstrip())
        if (
            not self._json_in_string
            and trailing_whitespace >= self.WHITESPACE_FLOOD
        ):
            return 1
        if self._pending < self.CHECK_EVERY or len(self._tail) < self.WINDOW:
            return None
        self._pending = 0
        return self._check()

    def _track_json_boundary(self, text: str) -> bool:
        """JSON 객체/배열 루트 종료 뒤 공백을 세어 조기에 중단한다.

        의미 JSON을 파싱·복구하지 않고 문자열/escape/괄호 경계만 추적한다. 완성된 루트
        뒤 비공백이 오면 유효 JSON이 아니므로 이 조기 경계는 해제하되 일반 반복 검사는
        계속한다.
        """
        for ch in text:
            if self._json_root_complete:
                if ch.isspace():
                    self._post_root_whitespace += 1
                    if self._post_root_whitespace >= self.POST_ROOT_WHITESPACE_FLOOD:
                        return True
                    continue
                self._json_root_complete = False
                self._post_root_whitespace = 0

            if not self._json_started:
                if ch in ("{", "["):
                    self._json_started = True
                    self._json_stack.append(ch)
                continue

            if self._json_in_string:
                if self._json_escaped:
                    self._json_escaped = False
                elif ch == "\\":
                    self._json_escaped = True
                elif ch == '"':
                    self._json_in_string = False
                continue

            if ch == '"':
                self._json_in_string = True
            elif ch in ("{", "["):
                self._json_stack.append(ch)
            elif ch in ("}", "]") and self._json_stack:
                opening = self._json_stack[-1]
                if (opening, ch) in (("{", "}"), ("[", "]")):
                    self._json_stack.pop()
                    if not self._json_stack:
                        self._json_root_complete = True
                        self._post_root_whitespace = 0
        return False

    def _check(self) -> Optional[int]:
        s = self._tail[-self.WINDOW:]
        # 자기중첩: s[p:] == s[:-p] ⟺ s 전체가 주기 p 로 정확 반복. 최소 p 를 반환.
        # 비주기 텍스트는 첫 글자 비교에서 대부분 탈락해 실비용이 낮다.
        for period in range(1, self.MAX_PERIOD + 1):
            if s[period:] == s[:-period]:
                return period
        return None


class RepetitionGuardCallback(AsyncCallbackHandler):
    """스트리밍 토큰마다 감지기를 돌려 폭주 시 raise → 스트림 중단(서버 abort).

    ``raise_error=True`` 필수 — 없으면 LangChain 콜백 매니저가 예외를 삼켜 로그만 남긴다.
    """

    raise_error = True

    def __init__(self, label: str) -> None:
        self.label = label
        self.detector = RepetitionDetector()

    async def on_chat_model_start(self, *args: Any, **kwargs: Any) -> None:
        # 에이전트처럼 한 invoke 에 LLM 콜이 여러 번이면 콜마다 새 판정 — 턴 경계의
        # 텍스트가 섞여 주기성 판정을 오염시키지 않게 한다.
        self.detector = RepetitionDetector()

    async def on_llm_new_token(self, token: str, *, chunk: Any = None, **kwargs: Any) -> None:
        # 구조화 출력이 tool-call delta로 흐르면 token/content는 빈 문자열이다.
        # ChatGenerationChunk.message와 AIMessageChunk 직접 전달을 모두 처리한다.
        text = _streamed_text(token, chunk)
        period = self.detector.feed(text)
        if period is not None:
            raise RepetitionLoopAborted(
                self.label,
                period,
                self.detector.chars_seen,
                partial_text=self.detector.tail_text,
            )
