"""LLM 배치 fan-out 헬퍼 — prefix 캐시 워밍업.

병렬 LLM 배치(함수 분석·모듈 요약 등)를 cold 상태에서 한꺼번에 보내면 —
서버의 prefix 캐시(RadixAttention 등)가 **전부 미스**한다. 아직 공유 prefix
(system 프롬프트 등)를 캐시에 올린 요청이 하나도 없기 때문이다.

해결 = **"1건 워밍 → 나머지 병렬"**: 첫 작업 하나를 단독 실행해 공유 prefix 를
캐시에 올린 뒤, 나머지를 병렬로 보낸다. 나머지는 그 prefix 에 적중한다.

이 워밍 패턴이 step5·6·7 의 LLM fan-out 여러 곳에 필요해서 — 복붙 대신 이 헬퍼
하나로 모은다.
"""
from __future__ import annotations

import asyncio
from typing import Iterable


async def gather_with_warmup(coros: Iterable) -> list:
    """첫 1건으로 prefix 캐시를 데운 뒤 나머지를 병렬 실행 → 결과 list.

    ``coros`` 는 prefix 를 공유하는 LLM 작업들 (보통 같은 yaml rule 을 여러 대상에
    적용). 첫 작업이 system 같은 공유 prefix 를 캐시에 올리면 —
    나머지 병렬 작업이 그 캐시에 적중한다.

    동시성 상한은 각 coro 안의 ``Semaphore`` 가 책임진다 (이 헬퍼는 워밍 순서만 관여).
    결과 순서는 입력 순서와 같다. 하나라도 실패하면 예외를 즉시 전파한다.
    """
    coros = list(coros)
    if not coros:
        return []

    # 워밍 — 첫 1건 단독 실행 (공유 prefix 를 캐시에 올림).
    try:
        first = await coros[0]
    except BaseException:
        # 첫 작업 실패 전파 — 아직 안 돌린 나머지 coro 는 닫아 RuntimeWarning 방지.
        for coro in coros[1:]:
            coro.close()
        raise

    if len(coros) == 1:
        return [first]

    # 나머지 병렬 — 데워진 캐시에 적중.
    tasks = [asyncio.create_task(coro) for coro in coros[1:]]
    try:
        rest = await asyncio.gather(*tasks)
    except BaseException:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise
    return [first, *rest]
