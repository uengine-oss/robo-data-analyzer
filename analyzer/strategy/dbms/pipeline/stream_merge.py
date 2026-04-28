"""여러 async generator 의 출력을 병렬 stream merge.

DBMS 파이프라인의 phase 간/phase 안 병렬화 공통 헬퍼.

사용 케이스:
  - phase_summary.run_phase_post: unit_summary + table_summary 동시 실행
  - dbms_pipeline.run_pipeline: Phase 4 (vector) + Phase 5 (lineage) 동시 실행
  - 기타 의존성 없는 phase 들의 동시 실행

각 generator 를 Task 로 발사하고 출력 chunk 를 단일 queue 로 모아 stream 으로 yield.
모든 generator 종료 시 gather 로 정리.
"""

from __future__ import annotations

import asyncio
import logging
from typing import AsyncGenerator, AsyncIterator, Tuple

from util.logger import log_process

logger = logging.getLogger(__name__)


async def merge_streams(
    *named_generators: Tuple[str, AsyncIterator[bytes]],
) -> AsyncGenerator[bytes, None]:
    """여러 async generator 를 병렬 실행하고 출력 chunk 를 stream merge.

    Args:
        named_generators: (label, async_generator) 튜플의 가변 인자.
            label 은 예외 errlog 용 식별자.

    Yields:
        모든 입력 generator 의 chunk 를 도착 순서로 yield.
    """
    if not named_generators:
        return

    queue: asyncio.Queue = asyncio.Queue()
    sentinel = object()

    async def _drain(label: str, gen: AsyncIterator[bytes]) -> None:
        try:
            async for chunk in gen:
                await queue.put(chunk)
        except Exception as e:
            log_process(
                "STREAM_MERGE", "ERROR",
                f"{label} 실행 실패: {e}",
                logging.ERROR, e,
            )
            raise
        finally:
            await queue.put((sentinel, label))

    tasks = [
        asyncio.create_task(_drain(label, gen))
        for label, gen in named_generators
    ]

    pending = len(tasks)
    while pending > 0:
        item = await queue.get()
        if isinstance(item, tuple) and len(item) == 2 and item[0] is sentinel:
            pending -= 1
            continue
        yield item

    await asyncio.gather(*tasks, return_exceptions=True)


__all__ = ["merge_streams"]
