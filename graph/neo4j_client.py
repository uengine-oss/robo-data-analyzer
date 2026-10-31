"""Neo4j async 클라이언트 — UNWIND 배치 + 재시도 + SIGTERM graceful close.

설계 원칙:
- 모든 노드 / 엣지 쓰기는 ``run_batch_unwind`` 통해 UNWIND 배치 (1건씩 INSERT X).
- TransientError / ServiceUnavailable 재시도 (tenacity 지수 백오프).
- Ontological DB 의 스키마 경합 오류도 재시도한다 (``is_retryable`` docstring).
- SIGTERM 시 driver close (운영기 graceful shutdown).
"""
from __future__ import annotations

import asyncio
import re
import signal
from typing import Any, Optional

from neo4j import AsyncGraphDatabase
from neo4j.exceptions import Neo4jError, ServiceUnavailable, TransientError
from tenacity import (
    AsyncRetrying,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

from shared.config.settings import SETTINGS
from shared.events.runtime_logger import log_warning
from graph.connection_context import get_override


# Ontological DB 는 라벨=타입·속성=열로 저장하고, 부모 라벨(:ANALYZER_OWNED) 조회를 하위 타입들의
# 합집합 뷰로 푼다. 다른 세션이 같은 순간 새 타입(첫 :CLASS 노드)이나 새 속성(첫 summary)을 만들면
# 그 뷰·열을 다시 만드는데, 이미 계획된 질의는 사라진 이름을 보고 실패한다 (실측 재현: 타입을 만드는
# 쓰기와 부모 라벨 MATCH 를 동시에 돌리면 ``relation "og_data.v_134" does not exist`` /
# ``column n2.p_v4 does not exist``). 실패한 문장은 통째로 롤백되고 우리 쓰기는 전부 MERGE/SET
# (멱등)이라 같은 문장을 다시 보내면 된다. step 들이 병렬 superstep 으로 돌아 이 경합은 매 run 생긴다.
_SCHEMA_RACE = re.compile(
    r'relation "og_[a-z_]+\.[^"]+" does not exist|column [a-z0-9_]+\.[a-z0-9_]+ does not exist'
)


# Ontological DB 에서 부모 라벨(:ANALYZER_OWNED) MATCH 는 하위 타입 전부의 열을 합친 뷰를 탄다.
# 그 뷰는 (1) 종류 간 같은 이름 속성의 값 타입이 다르면 깨지고(``UNION types bigint and text
# cannot be matched``), (2) 합친 속성이 50개를 넘으면 노드 전체 행을 만드는 순간 — SET·REMOVE·
# ``properties(n)``·그 노드로 관계 MERGE — 실패한다(``cannot pass more than 100 arguments to a
# function``; 이 hanbit-legacy 분석이 끝나면 이미 54개). 무라벨 ``MATCH (n {id, graph_owner})`` 는
# 셋 다 없다(실측). Neo4j 에선 그 반대로 무라벨 MATCH 가 AllNodesScan 이라(AST 적재 159초 원인)
# 라벨을 지워선 안 된다. 그래서 질의 원문은 Neo4j 형태로 두고 Ontological 로 보낼 때만 바꾼다.
# MERGE 는 바꾸지 않는다 — 만들 때 라벨 사슬을 다 줘야 하고(``:ANALYZER_OWNED:<종류>``), 그
# 매칭은 구체 타입 하나만 본다.
_OWNED_MATCH = re.compile(r"(MATCH\s*\(\s*[A-Za-z_]\w*)\s*:\s*ANALYZER_OWNED(?=\s*\{)")
_DIALECT_BY_TARGET: dict[tuple[str, str], str] = {}


def for_ontological(query: str) -> str:
    """``MATCH (x:ANALYZER_OWNED {…})`` → ``MATCH (x {…})`` (MERGE·다중 라벨 MATCH 는 그대로)."""
    return _OWNED_MATCH.sub(r"\1", query)


def dialect_of_agent(agent: str) -> str:
    """Bolt HELLO 의 server agent → 방언. Ontological Bolt 게이트웨이는 agent 에 이름을 싣는다."""
    return "ontological" if "ontolog" in (agent or "").lower() else "neo4j"


def is_retryable(error: BaseException) -> bool:
    """재시도해도 되는 오류 — 일시 장애·연결 끊김, 그리고 Ontological DB 스키마 경합."""
    if isinstance(error, (TransientError, ServiceUnavailable)):
        return True
    return isinstance(error, Neo4jError) and bool(_SCHEMA_RACE.search(str(error.message or error)))


class Neo4jClient:
    """Neo4j async 클라이언트.

    연결 출처: 현재 run 컨텍스트에 Neo4j override(Electron 호스트가 헤더로 주입)가 있으면
    그 값을, 없으면(브라우저/테스트/CLI) ``SETTINGS.graph_database``(.env) 를 쓴다.

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
            self._database = override.database or SETTINGS.graph_database.database
        else:
            uri, user, password = SETTINGS.graph_database.uri, SETTINGS.graph_database.user, SETTINGS.graph_database.password
            self._database = SETTINGS.graph_database.database
        self._driver = AsyncGraphDatabase.driver(uri, auth=(user, password))
        self._target = (uri, self._database)
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

        query = await self._adapt(query)
        async for attempt in self._retrying():
            with attempt:
                async with self._driver.session(database=self._database) as session:
                    result = await session.run(query, items=items)
                    return await result.consume()

    async def run_query(self, query: str, params: Optional[dict] = None) -> Any:
        """단발 쿼리 (인덱스 / 제약 / 1회성 lookup)."""
        query = await self._adapt(query)
        async for attempt in self._retrying():
            with attempt:
                async with self._driver.session(database=self._database) as session:
                    result = await session.run(query, **(params or {}))
                    return await result.consume()

    async def run_read_query(self, query: str, params: Optional[dict] = None) -> list[dict]:
        """읽기 쿼리 — record 들을 dict list 로 반환 (graph 도구가 사용)."""
        query = await self._adapt(query)
        async for attempt in self._retrying():
            with attempt:
                async with self._driver.session(database=self._database) as session:
                    result = await session.run(query, **(params or {}))
                    return await result.data()

    async def dialect(self) -> str:
        """``neo4j`` / ``ontological`` — 설정이 auto 면 서버 agent 로 한 번 판별해 대상별로 기억한다."""
        configured = SETTINGS.graph_database.dialect
        if configured != "auto":
            return configured
        known = _DIALECT_BY_TARGET.get(self._target)
        if known is not None:
            return known
        try:
            info = await self._driver.get_server_info()
        except Exception as error:  # noqa: BLE001 — 판별 실패는 다음 호출에서 다시 묻는다
            log_warning(
                "graph.neo4j_client",
                "dialect_probe",
                "그래프 엔진 판별 실패 — 이번 질의는 Neo4j 형태로 보낸다",
                error=f"{type(error).__name__}: {error}",
            )
            return "neo4j"
        _DIALECT_BY_TARGET[self._target] = dialect_of_agent(info.agent)
        return _DIALECT_BY_TARGET[self._target]

    async def _adapt(self, query: str) -> str:
        return for_ontological(query) if await self.dialect() == "ontological" else query

    @staticmethod
    def _retrying() -> AsyncRetrying:
        return AsyncRetrying(
            stop=stop_after_attempt(SETTINGS.graph_database.retry_max_attempts),
            wait=wait_exponential(multiplier=SETTINGS.graph_database.retry_backoff_seconds),
            retry=retry_if_exception(is_retryable),
            before_sleep=_log_retry,
            reraise=True,
        )


def _log_retry(retry_state) -> None:
    """재시도 사실을 남긴다 — 조용히 삼키면 경합이 잦아져도 보이지 않는다."""
    error = retry_state.outcome.exception() if retry_state.outcome else None
    log_warning(
        "graph.neo4j_client",
        "retry",
        "그래프 질의 재시도",
        attempt=retry_state.attempt_number,
        error=f"{type(error).__name__}: {str(error)[:200]}" if error else "",
    )
