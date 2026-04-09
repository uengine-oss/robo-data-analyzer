"""비동기 배치 실행기 + LLM Rate Limiter.

모든 병렬 LLM 호출과 배치 처리에 사용되는 공용 인프라.
Threading/ThreadPoolExecutor 대신 asyncio 기반으로 통일.

사용 예:
    limiter = LLMRateLimiter(max_concurrent=50, spacing_ms=50)
    executor = AsyncBatchExecutor(limiter)

    results = await executor.run_batch(
        items=chunks,
        processor=lambda chunk: PromptRegistry.call_extract(chunk, lang, api_key),
    )
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Awaitable, Callable, List, Optional, TypeVar

logger = logging.getLogger(__name__)

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
            except Exception as e:
                failed += 1
                logger.error(
                    "[BATCH:%s] 아이템 처리 실패 | idx=%d/%d error=%s",
                    label or "UNKNOWN", idx + 1, total, e,
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

    async def run_parallel(self, *coroutines: Awaitable[Any], label: str = "") -> List[Any]:
        """여러 코루틴을 병렬 실행. 개별 실패는 예외 객체로 반환.

        Args:
            *coroutines: 병렬 실행할 코루틴들
            label: 로깅용 라벨

        Returns:
            결과 리스트 (실패한 것은 Exception 객체)
        """
        results = await asyncio.gather(*coroutines, return_exceptions=True)
        for i, r in enumerate(results):
            if isinstance(r, Exception):
                logger.error("[PARALLEL:%s] 코루틴 %d 실패 | error=%s", label, i, r, exc_info=r)
        return list(results)


# ── 전역 인스턴스 (settings에서 설정값 로드) ──

_global_limiter: Optional[LLMRateLimiter] = None
_global_executor: Optional[AsyncBatchExecutor] = None


def get_rate_limiter() -> LLMRateLimiter:
    """전역 LLMRateLimiter 싱글톤."""
    global _global_limiter
    if _global_limiter is None:
        from config.settings import settings
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
