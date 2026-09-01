"""Small async Neo4j client used only by final graph publication."""
from __future__ import annotations

import asyncio
import signal

from neo4j import AsyncGraphDatabase

from graph.connection_context import get_override
from shared.config.app_settings import SETTINGS
from shared.events.runtime_logger import log_warning


class Neo4jClient:
    def __init__(self):
        override = get_override()
        if override is not None:
            uri, user, password = override.uri, override.user, override.password
            self._database = override.database or SETTINGS.graph_database.database
        else:
            database = SETTINGS.graph_database
            uri, user, password = database.uri, database.user, database.password
            self._database = database.database
        self._driver = AsyncGraphDatabase.driver(uri, auth=(user, password))
        self._closed = False
        try:
            signal.signal(signal.SIGTERM, self._on_sigterm)
        except (ValueError, OSError) as error:
            log_warning(
                "graph.neo4j_client",
                "register_sigterm",
                "SIGTERM handler registration failed",
                error=f"{type(error).__name__}: {error}",
            )

    def _on_sigterm(self, *_args) -> None:
        try:
            asyncio.get_running_loop().create_task(self.close())
        except RuntimeError as error:
            log_warning(
                "graph.neo4j_client",
                "close_on_sigterm",
                "event loop is unavailable",
                error=str(error),
            )

    async def run_query(self, query: str) -> None:
        async with self._driver.session(database=self._database) as session:
            result = await session.run(query)
            await result.consume()

    async def execute_write(self, work):
        async with self._driver.session(database=self._database) as session:
            return await session.execute_write(work)

    async def close(self) -> None:
        if not self._closed:
            await self._driver.close()
            self._closed = True
