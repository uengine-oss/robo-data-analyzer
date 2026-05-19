"""Neo4j async 클라이언트 — UNWIND 배치 + 재시도 + SIGTERM graceful close.

설계 원칙:
- 모든 노드 / 엣지 쓰기는 ``run_batch_unwind`` 통해 UNWIND 배치 (1건씩 INSERT X).
- TransientError / ServiceUnavailable 재시도 (tenacity 지수 백오프).
- SIGTERM 시 driver close (운영기 graceful shutdown).
"""
from __future__ import annotations

import asyncio
import signal
from typing import Any, Optional

from neo4j import AsyncGraphDatabase
from neo4j.exceptions import ServiceUnavailable, TransientError
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from shared.config.env_catalog import ENV
from shared.logging_and_stream.logger import log_warning


class Neo4jClient:
    """Neo4j async 클라이언트.

    Usage::

        client = Neo4jClient()
        await client.run_batch_unwind(query, items)
        ...
        await client.close()
    """

    def __init__(self):
        self._driver = AsyncGraphDatabase.driver(
            ENV.neo4j.uri,
            auth=(ENV.neo4j.user, ENV.neo4j.password),
        )
        self._database = ENV.neo4j.database
        self._closed = False

        # SIGTERM graceful close (메인 스레드에서만 가능 — 백그라운드 thread 면 실패).
        # 실패해도 운영 진행 가능 (서버 강제 종료 시 driver 자동 정리됨) — 그러나 사유는 명시.
        try:
            signal.signal(signal.SIGTERM, self._on_sigterm)
        except (ValueError, OSError) as e:
            log_warning(
                "shared.neo4j.neo4j_client",
                "register_sigterm",
                "SIGTERM 핸들러 등록 실패 — 비정상 종료 시 driver 강제 close 됨",
                error=f"{type(e).__name__}: {e}",
            )

    def _on_sigterm(self, *_args) -> None:
        """SIGTERM 수신 시 driver close — event loop 없으면 graceful X."""
        try:
            asyncio.get_event_loop().create_task(self.close())
        except RuntimeError as e:
            log_warning(
                "shared.neo4j.neo4j_client",
                "on_sigterm",
                "event loop 없음 — driver close 못 함 (즉시 종료 시 connection leak 가능)",
                error=str(e),
            )

    async def close(self) -> None:
        if not self._closed:
            await self._driver.close()
            self._closed = True

    async def run_batch_unwind(self, query: str, items: list[dict]) -> Any:
        """``UNWIND $items AS item ...`` 형식 쿼리 배치 실행 (재시도 포함).

        items 빈 list 면 즉시 None 반환 (불필요한 호출 차단).
        ``ROBO_NEO4J_UNWIND_BATCH_SIZE`` 보다 크면 호출자가 사전 분할해야 함.
        """
        if not items:
            return None

        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(ENV.neo4j.retry_max_attempts),
            wait=wait_exponential(multiplier=ENV.neo4j.retry_backoff_seconds),
            retry=retry_if_exception_type((TransientError, ServiceUnavailable)),
            reraise=True,
        ):
            with attempt:
                async with self._driver.session(database=self._database) as session:
                    result = await session.run(query, items=items)
                    return await result.consume()

    async def run_query(self, query: str, params: Optional[dict] = None) -> Any:
        """단발 쿼리 (인덱스 / 제약 / 1회성 lookup)."""
        async with self._driver.session(database=self._database) as session:
            result = await session.run(query, **(params or {}))
            return await result.consume()

    async def run_read_query(self, query: str, params: Optional[dict] = None) -> list[dict]:
        """읽기 쿼리 — record 들을 dict list 로 반환 (graph 도구가 사용)."""
        async with self._driver.session(database=self._database) as session:
            result = await session.run(query, **(params or {}))
            return await result.data()
