"""생성 중 반복 폭주 실시간 차단 — 스트리밍 청크의 결정론 주기성 검사 (spec 080).

배경: 약한 모델 + 탐욕 디코딩에서 "비슷한 항목의 긴 목록 + 끝점 없는 출력" 이
공백/문구 반복 루프로 폭주한다(사건 A·B·C 실측). 기존 방어는 사후(상한·품질 게이트)라
피해가 수십~백여 초. 이 모듈은 생성 **중** 반복 궤도를 감지해 초 단위로 끊는다.
서버 네이티브 차단(`no_repeat_ngram_size`)은 사내 sglang 빌드 미지원 실측(500) → 클라이언트 구현.

감지 규칙은 단 하나(결정론): 누적 출력의 접미 창 ``WINDOW`` 자가 주기 ``p ≤ MAX_PERIOD``
로 **정확히** 반복되면 폭주다. 주기 판정은 자기중첩 ``s[p:] == s[:-p]`` (⟺ s 의 주기가 p)
슬라이스 비교로 한다 — C 구현 memcmp 라 검사당 1ms 미만이고, 주기가 창 길이의 약수가
아니어도 정확하다(회전 트릭은 약수 조건이 필요해 부적합 — 단위테스트로 반증됨).
공백 폭주는 주기 1로 자동 포함된다.

오탐 안전 논거: 정상 구조화 출력은 항목마다 내용이 달라 3,072자를 정확히 주기 반복할 수
없다(JSON 키 반복은 사이에 서로 다른 내용이 끼어 비주기). 창보다 짧은 출력은 검사가
시작되지 않아 한줄요약 등 소형 룰은 원천 면제다.
"""
from __future__ import annotations

from typing import Any, Optional

from langchain_core.callbacks import AsyncCallbackHandler


class RepetitionLoopAborted(RuntimeError):
    """반복 폭주 감지로 생성을 조기 중단 — 호출부 예외 경로로 흡수(부분 표시·무중단)."""

    def __init__(self, label: str, period: int, chars_seen: int) -> None:
        self.label = label
        self.period = period
        self.chars_seen = chars_seen
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

    def __init__(self) -> None:
        self._tail = ""
        self._pending = 0
        self.chars_seen = 0

    def feed(self, text: str) -> Optional[int]:
        if not text:
            return None
        self.chars_seen += len(text)
        self._tail = (self._tail + text)[-self.TAIL_KEEP:]
        self._pending += len(text)
        if self._pending < self.CHECK_EVERY or len(self._tail) < self.WINDOW:
            return None
        self._pending = 0
        return self._check()

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
        text = token
        if not text and chunk is not None:
            # 구조화 출력이 tool-call 델타로 흐르면 token 이 빈 문자열일 수 있다.
            text = getattr(chunk, "text", "") or ""
        period = self.detector.feed(text)
        if period is not None:
            raise RepetitionLoopAborted(self.label, period, self.detector.chars_seen)
