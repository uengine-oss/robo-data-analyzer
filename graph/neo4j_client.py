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

from system.env_catalog import ENV
from log.logger import log_warning
from graph.connection_context import get_override


class Neo4jClient:
    """Neo4j async 클라이언트.

    연결 출처: 현재 run 컨텍스트에 Neo4j override(Electron 호스트가 헤더로 주입)가 있으면
    그 값을, 없으면(브라우저/테스트/CLI) ``ENV.neo4j``(.env) 를 쓴다.

    Usage::

        client = Neo4jClient()
        await client.run_batch_unwind(query, items)
        ...
        await client.close()
    """

    def __init__(self):
        override = get_override()
        if override is not None:
            uri, user, password = override.uri, override.user, override.password
            self._database = override.database or ENV.neo4j.database
        else:
            uri, user, password = ENV.neo4j.uri, ENV.neo4j.user, ENV.neo4j.password
            self._database = ENV.neo4j.database
        self._driver = AsyncGraphDatabase.driver(uri, auth=(user, password))
        self._closed = False

        # SIGTERM graceful close (메인 스레드에서만 가능 — 백그라운드 thread 면 실패).
        # 실패해도 운영 진행 가능 (서버 강제 종료 시 driver 자동 정리됨) — 그러나 사유는 명시.
        try:
            signal.signal(signal.SIGTERM, self._on_sigterm)
        except (ValueError, OSError) as e:
            log_warning(
                "graph.neo4j_client",
                "register_sigterm",
                "SIGTERM 핸들러 등록 실패 — 비정상 종료 시 driver 강제 close 됨",
                error=f"{type(e).__name__}: {e}",
            )

    def _on_sigterm(self, *_args) -> None:
        """SIGTERM 수신 시 driver close — event loop 없으면 graceful X."""
        try:
            asyncio.get_running_loop().create_task(self.close())
        except RuntimeError as e:
            log_warning(
                "graph.neo4j_client",
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

        **배치 분할은 하지 않는다** — 옛 docstring 은 ``ROBO_NEO4J_UNWIND_BATCH_SIZE`` 를
        언급했으나 그 설정은 **읽히지도 강제되지도 않았다**(문서가 거짓말). 설정은 삭제했고
        (spec 046 FR-010), 실제 동작은 "받은 items 를 한 번에 UNWIND" 다.
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
