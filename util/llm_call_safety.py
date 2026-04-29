"""LLM 호출 안전 인프라 — 가드레일 + 재시도 분류 + 피드백 + Rate Limiter.

원본 위치 (이전):
  - `util/llm_guardrails.py` (sanitize / retry 분류 / 피드백)
  - `util/llm_rate_limiter.py` (LLMRateLimiter / AsyncBatchExecutor)

책임 (한 도메인 = 한 파일 — LLM 호출 보호 인프라):
  1. 응답 오염 제거 (sanitize_*) — 제어문자·코드펜스·JSON 주석·잘못된 escape
  2. 재시도 분류 (is_parse_failure / is_rate_limit / should_retry)
  3. 재시도 피드백 (build_retry_feedback) — LLM 에 직전 오류 알려 수정 유도
  4. 동시성 제어 (LLMRateLimiter) — Semaphore + spacing 으로 throttling 방지
  5. 비동기 배치 실행기 (AsyncBatchExecutor) — items 병렬 처리

모델별 특수 처리는 모델·경로별 위치에 두고, 여기는 언어·모델 무관 공통 로직만.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Awaitable, Callable, List, Optional, TypeVar

from config.settings import settings

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════
# Section 1: Sanitize — LLM 응답의 흔한 오염 제거
# ═══════════════════════════════════════════════════════════════════════════

# JSON spec 상 유효한 escape
_VALID_JSON_ESCAPE_CHARS = frozenset('"\\/bfnrt')

# 제어 문자 (\x00~\x1f 중 tab/newline 제외)
_CONTROL_CHAR_RE = re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f]')

# ChatML/OpenAI 계열 특수 토큰 — 서버 변환 실패 시 content 에 raw leak 되는 경우 방어.
# 일반적인 `<|name|>` 형태를 모두 제거 (모델 무관 — 정상 응답에는 안 나타남).
_CONTROL_TOKEN_RE = re.compile(r'<\|[a-zA-Z_]+\|>')

# JSON 주석 — 문자열 리터럴 내부 `//` 는 보존 (alternation 으로 string literal 먼저 매칭)
_JSON_LINE_COMMENT_RE = re.compile(r'("(?:[^"\\]|\\.)*")|//[^\n]*')
_JSON_BLOCK_COMMENT_RE = re.compile(r'("(?:[^"\\]|\\.)*")|/\*[\s\S]*?\*/')


def fix_invalid_json_escapes(s: str) -> str:
    """JSON 에서 허용되지 않는 escape 를 수리 (예: `\\x` → `x`).

    LLM 이 가끔 `\\q` 같은 유효하지 않은 escape 를 내보내 파서가 튕기는 걸 방지.
    """
    if not s or '\\' not in s:
        return s
    result = []
    i = 0
    while i < len(s):
        if s[i] == '\\' and i + 1 < len(s):
            nxt = s[i + 1]
            if nxt in _VALID_JSON_ESCAPE_CHARS:
                result.append(s[i:i + 2])
                i += 2
            elif nxt == 'u' and i + 5 < len(s):
                result.append(s[i:i + 6])
                i += 6
            else:
                result.append(nxt)
                i += 2
        else:
            result.append(s[i])
            i += 1
    return ''.join(result)


def strip_code_fences(s: str) -> str:
    """코드펜스(```json ... ```) 와 내부 백틱 제거."""
    stripped = s.strip()
    if stripped.startswith("```"):
        nl = stripped.find("\n")
        if nl != -1:
            stripped = stripped[nl + 1:]
        if stripped.endswith("```"):
            stripped = stripped[:-3]
        stripped = stripped.strip()
    return stripped.replace("`", "")


def strip_control_tokens(s: str) -> str:
    """ChatML 계열 제어 토큰(`<|start|>`, `<|end|>`, `<|message|>` 등) 제거."""
    return _CONTROL_TOKEN_RE.sub('', s)


def strip_json_comments(s: str) -> str:
    """JSON 주석(`//`, `/* */`) 제거. 문자열 리터럴 내부 `//` 는 보존."""
    s = _JSON_LINE_COMMENT_RE.sub(lambda m: m.group(1) if m.group(1) else '', s)
    s = _JSON_BLOCK_COMMENT_RE.sub(lambda m: m.group(1) if m.group(1) else '', s)
    return s


def sanitize_llm_output(text):
    """LLM 응답 공용 sanitize. AIMessage 객체 / 순수 str 모두 지원.

    순서: 제어문자 → 제어토큰 → 코드펜스 → JSON 주석 → escape 수리.
    """
    if hasattr(text, 'content'):
        if isinstance(text.content, list):
            text.content = "".join(
                block.get("text", "") if isinstance(block, dict) else str(block)
                for block in text.content
            )
        text.content = _clean(text.content)
        return text
    if isinstance(text, str):
        return _clean(text)
    return text


def _clean(s: str) -> str:
    s = _CONTROL_CHAR_RE.sub('', s)
    s = strip_control_tokens(s)
    s = strip_code_fences(s)
    s = strip_json_comments(s)
    return fix_invalid_json_escapes(s)


# ═══════════════════════════════════════════════════════════════════════════
# Section 2: 재시도 분류 — 어떤 예외가 재시도 대상인지
# ═══════════════════════════════════════════════════════════════════════════


def is_parse_failure(error: Exception) -> bool:
    """JSON 파싱 실패인지 판정."""
    err = str(error)
    return (
        "Invalid json output" in err
        or "OUTPUT_PARSING_FAILURE" in err
        or "OutputParserException" in err
    )


def is_rate_limit(error: Exception) -> bool:
    """429 / rate / quota 계열 판정."""
    err = str(error).lower()
    return "429" in err or "rate" in err or "quota" in err


def is_bad_request_parse(error: Exception) -> bool:
    """400 Bad Request + parse 관련 힌트 판정 (서버측 입력 파싱 실패)."""
    err = str(error).lower()
    return "400" in err and "parse" in err


def should_retry(error: Exception) -> bool:
    """이 예외가 재시도 대상인지 공용 판정."""
    return is_rate_limit(error) or is_bad_request_parse(error) or is_parse_failure(error)


# ═══════════════════════════════════════════════════════════════════════════
# Section 3: 재시도 피드백 — 이전 오류를 LLM 에게 알려 수정 유도
# ═══════════════════════════════════════════════════════════════════════════


def build_retry_feedback(
    error: Exception,
    attempt: int,
    *,
    max_err_len: int = 400,
    response_tail: str = "",
    tail_len: int = 200,
) -> str:
    """재시도용 피드백 블록. 기존 프롬프트 끝에 덧붙여 재호출하면 LLM 이 오류를 인식해 수정.

    response_tail 에 실패한 응답의 마지막 N 자를 넘기면 "어디서 깨졌는지" 까지 LLM 에 보여줘
    수정 정확도가 올라간다.
    """
    err_str = str(error)[:max_err_len]
    tail_block = ""
    if response_tail:
        clipped = response_tail[-tail_len:]
        tail_block = (
            f"\n응답이 이 지점에서 깨졌습니다 (마지막 {len(clipped)}자):\n"
            f"...{clipped}\n"
        )
    return (
        "---\n"
        f"⚠️ 이전 시도 {attempt} 의 응답이 파싱에 실패했습니다. 원인:\n"
        f"  {err_str}"
        f"{tail_block}\n"
        "다음 규칙을 반드시 지켜 **처음부터 다시** 응답해주세요:\n"
        "- 순수 JSON 객체만 출력. 앞뒤 설명·코드펜스·부가 텍스트 금지.\n"
        "- `//` 또는 `/* */` 주석 금지. 불확실한 값은 생략하거나 description 필드에 자연어로.\n"
        "- 트레일링 콤마 금지.\n"
        "- 문자열 내부 double quote 는 `\\\"`, backslash 는 `\\\\` 로 escape.\n"
    )


# ═══════════════════════════════════════════════════════════════════════════
# Section 4: Rate Limiter — 동시성 제어 + 요청 간격 보장
# ═══════════════════════════════════════════════════════════════════════════


T = TypeVar("T")
R = TypeVar("R")


class LLMRateLimiter:
    """전역 LLM 호출 동시성 제어.

    - Semaphore로 최대 동시 요청 수 제한
    - 요청 간 최소 간격(spacing) 적용하여 OpenAI throttling 방지
    - 모든 LLM 호출이 이 인스턴스를 공유해야 함
    """

    def __init__(self, max_concurrent: int = 50, spacing_ms: int = 50):
        self._sem = asyncio.Semaphore(max_concurrent)
        self._spacing = spacing_ms / 1000.0
        self._lock = asyncio.Lock()
        self._last_call_time = 0.0

    async def __aenter__(self):
        await self._sem.acquire()
        # 요청 간 최소 간격 보장
        async with self._lock:
            now = asyncio.get_event_loop().time()
            elapsed = now - self._last_call_time
            if elapsed < self._spacing:
                await asyncio.sleep(self._spacing - elapsed)
            self._last_call_time = asyncio.get_event_loop().time()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        self._sem.release()
        return False


class AsyncBatchExecutor:
    """비동기 배치 실행기.

    items를 병렬 처리하되, rate_limiter로 동시성을 제어.
    개별 아이템 실패 시 None 반환 + 에러 로그 (배치 전체 중단 안 함).
    """

    def __init__(self, rate_limiter: Optional[LLMRateLimiter] = None):
        self._limiter = rate_limiter

    async def run_batch(
        self,
        items: List[T],
        processor: Callable[[T], Awaitable[R]],
        on_result: Optional[Callable[[R], None]] = None,
        label: str = "",
    ) -> List[Optional[R]]:
        """items를 병렬 처리. 각 호출은 rate_limiter를 통과.

        Args:
            items: 처리할 아이템 리스트
            processor: 각 아이템을 처리하는 async 함수
            on_result: 각 결과가 나올 때마다 호출되는 콜백
            label: 로깅용 라벨

        Returns:
            결과 리스트 (실패한 아이템은 None)
        """
        if not items:
            return []

        total = len(items)
        completed = 0
        failed = 0

        async def _process_one(idx: int, item: T) -> Optional[R]:
            nonlocal completed, failed
            try:
                if self._limiter:
                    async with self._limiter:
                        result = await processor(item)
                else:
                    result = await processor(item)
                completed += 1
                if on_result:
                    on_result(result)
                return result
            except Exception as e:  # noqa: SILENT_FAIL — 한 아이템 실패가 다른 아이템 진행을 막지 않도록 errlog 후 None 반환 (호출자가 None 체크)
                failed += 1
                logger.error(
                    "[BATCH:%s] 아이템 처리 실패 | idx=%d/%d error=%s",
                    label or "UNKNOWN", idx + 1, total, e,  # noqa: FALLBACK_DEFAULT — 로그 라벨 utility default
                    exc_info=True,
                )
                return None

        tasks = [asyncio.create_task(_process_one(i, item)) for i, item in enumerate(items)]
        results = await asyncio.gather(*tasks)

        if failed:
            logger.error("[BATCH:%s] 배치 완료 | total=%d completed=%d failed=%d", label, total, completed, failed)
        else:
            logger.info("[BATCH:%s] 배치 완료 | total=%d", label, total)

        return list(results)


# ── 전역 인스턴스 (settings에서 설정값 로드) ──

_global_limiter: Optional[LLMRateLimiter] = None
_global_executor: Optional[AsyncBatchExecutor] = None


def get_rate_limiter() -> LLMRateLimiter:
    """전역 LLMRateLimiter 싱글톤."""
    global _global_limiter
    if _global_limiter is None:
        _global_limiter = LLMRateLimiter(
            max_concurrent=settings.concurrency.max_concurrency,
            spacing_ms=50,  # OpenAI throttling 방지용 기본값
        )
    return _global_limiter


def get_executor() -> AsyncBatchExecutor:
    """전역 AsyncBatchExecutor 싱글톤."""
    global _global_executor
    if _global_executor is None:
        _global_executor = AsyncBatchExecutor(rate_limiter=get_rate_limiter())
    return _global_executor


__all__ = [
    # Sanitize
    "fix_invalid_json_escapes",
    "strip_code_fences",
    "strip_control_tokens",
    "strip_json_comments",
    "sanitize_llm_output",
    # Retry classification
    "is_parse_failure",
    "is_rate_limit",
    "is_bad_request_parse",
    "should_retry",
    # Retry feedback
    "build_retry_feedback",
    # Rate limiter
    "LLMRateLimiter",
    "AsyncBatchExecutor",
    "get_rate_limiter",
    "get_executor",
]
