"""LLM 배치 fan-out 헬퍼 — prefix 캐시 워밍업.

병렬 LLM 배치(함수 분석·모듈 요약 등)를 cold 상태에서 한꺼번에 보내면 —
서버의 prefix 캐시(RadixAttention 등)가 **전부 미스**한다. 아직 공유 prefix
(system 프롬프트·codebase_notes 등)를 캐시에 올린 요청이 하나도 없기 때문이다.

해결 = **"1건 워밍 → 나머지 병렬"**: 첫 작업 하나를 단독 실행해 공유 prefix 를
캐시에 올린 뒤, 나머지를 병렬로 보낸다. 나머지는 그 prefix 에 적중한다.

이 워밍 패턴이 step5·6·7 의 LLM fan-out 여러 곳에 필요해서 — 복붙 대신 이 헬퍼
하나로 모은다.
"""
from __future__ import annotations

import asyncio
from typing import Any, Callable, Iterable, Literal, Optional

from llm.client.usage.cost_budget import BudgetExceededError
from shared.log.logger import log_error


async def gather_with_warmup(coros: Iterable, *, return_exceptions: bool = False) -> list:
    """첫 1건으로 prefix 캐시를 데운 뒤 나머지를 병렬 실행 → 결과 list.

    ``coros`` 는 prefix 를 공유하는 LLM 작업들 (보통 같은 yaml rule 을 여러 대상에
    적용). 첫 작업이 system·codebase_notes 같은 공유 prefix 를 캐시에 올리면 —
    나머지 병렬 작업이 그 캐시에 적중한다.

    동시성 상한은 각 coro 안의 ``Semaphore`` 가 책임진다 (이 헬퍼는 워밍 순서만 관여).
    반환·``return_exceptions`` 의미는 ``asyncio.gather`` 와 같다 (결과 순서 = 입력 순서).
    """
    coros = list(coros)
    if not coros:
        return []

    # 워밍 — 첫 1건 단독 실행 (공유 prefix 를 캐시에 올림).
    if return_exceptions:
        try:
            first = await coros[0]
        except BaseException as exc:  # noqa: BLE001 — gather(return_exceptions=True) 동형
            first = exc
    else:
        try:
            first = await coros[0]
        except BaseException:
            # 첫 작업 실패 전파 — 아직 안 돌린 나머지 coro 는 닫아 RuntimeWarning 방지.
            for c in coros[1:]:
                c.close()
            raise

    if len(coros) == 1:
        return [first]

    # 나머지 병렬 — 데워진 캐시에 적중.
    rest = await asyncio.gather(*coros[1:], return_exceptions=return_exceptions)
    return [first, *rest]


def partition_llm_results(
    items: list,
    results: list,
    *,
    component: str,
    operation: str,
    id_of: Optional[Callable[[Any], str]] = None,
    on_failure: Literal["drop", "keep", "none"] = "drop",
) -> tuple[list, int]:
    """fan-out 결과에 **fail 정책**을 적용 — 단일 정의처 (spec 046 FR-011).

    ``gather_with_warmup(..., return_exceptions=True)`` 의 결과에는 성공값과 예외가 섞여 있다.
    그 분류 규칙은 어디서나 같다:

    1. ``BudgetExceededError`` → **즉시 raise** (헌법의 회로 차단기 — "$400 사고" 방지).
    2. 그 외 예외 → ``log_error`` + 실패 집계 (조용한 삼킴 금지, 헌법 IV).
    3. 성공 → 결과 사용.

    이 3줄이 step5·6·7 여러 곳에 복붙돼 있었다. 한 곳으로 모은다.

    Args:
        items: 입력 항목들 (결과와 **같은 순서·같은 길이**).
        results: ``return_exceptions=True`` fan-out 결과.
        component / operation: 로그 좌표.
        id_of: 실패 로그에 남길 항목 식별자 추출기 (없으면 생략).
        on_failure: 실패 항목의 처리 —
            ``"drop"`` 제외 / ``"keep"`` 원본 항목 유지(분석 흐름 보존) / ``"none"`` None 자리표시.

    Returns:
        ``(values, failed_count)``. ``on_failure`` 가 ``"keep"``/``"none"`` 이면
        ``len(values) == len(items)`` 가 보장된다 (자리 보존).
    """
    values: list = []
    failed = 0
    for item, result in zip(items, results):
        if isinstance(result, BudgetExceededError):
            raise result                       # 회로 차단기 — 상위로 그대로 전파
        if isinstance(result, BaseException):
            failed += 1
            ctx = {"item_id": id_of(item)} if id_of else {}
            log_error(component, operation, result, **ctx)
            if on_failure == "keep":
                values.append(item)
            elif on_failure == "none":
                values.append(None)
            continue
        values.append(result)
    return values, failed
