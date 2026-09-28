"""분석이 대상 graph 를 덮어쓰는 동안 다른 실행을 막는다.

## 왜 필요한가

분석은 대상 graph 에서 ``_owner = GRAPH_OWNER`` 인 노드·관계를 모두 지우고 다시
쓴다(``product_writer._replace``). PC 마다 DB 가 따로일 때는 드러나지 않는다.
**DB 를 공유하는 구성에서는 두 사람이 같은 프로젝트를 분석하면 서로의 결과를
지운다.** 오류는 나지 않는다 — 나중에 본 사람이 앞사람 결과가 사라진 것을
발견할 뿐이다.

## 왜 프로세스 락으로는 안 되나

``run_registry`` 의 "동시 분석 1개" 는 **이 컨테이너 안에서만** 유효하다. 각 PC 가
자기 analyzer 를 띄우므로 그 락들은 서로를 보지 못한다. 공유된 것은 DB 뿐이므로
락도 거기 있어야 한다.

## 왜 MERGE 가 아니고 제약인가 (실측, 2026-09-28)

Ontological Bolt 게이트웨이에서 ``MERGE`` + compare-and-set 은 **직렬화되지
않는다** — 동시 8회에 8개가 모두 "내가 획득했다" 고 답했다. 락으로 쓸 수 없다.

유일성 제약은 강제된다 — 동시 ``CREATE`` 8회 중 1개만 성공하고 7개가 거부되며
노드도 하나만 남는다. 그래서 **제약 + CREATE** 로 만든다.

## 제약 위반이 ConstraintError 로 오지 않는다 (실측)

게이트웨이가 돌려주는 코드는 ``Neo.ClientError.Statement.ArgumentError`` 이고
드라이버는 ``ClientError`` 를 던진다. ``ConstraintError`` 를 잡으면 **락이 통째로
안 걸린다**(예외가 그냥 올라가 분석이 실패한다). 그래서 메시지로 가른다.

## 락 노드가 화면에 새지 않는 이유

``_owner`` 를 설정하지 않는다. 그러면 세 곳에서 모두 제외된다.

* ``product_writer._replace`` 의 삭제는 ``_owner = 'analyzer'`` 범위다 — 지워지지
  않으므로 wipe 를 견딘다.
* Catalog 의 전체 그래프 읽기는 ``_owner = 'analyzer'`` 로 거른다
  (``graph/scope.py`` 의 ``owner_predicate``).
* analyzer 자신의 읽기도 ``_owner:'analyzer'`` 를 명시한다.
"""
from __future__ import annotations

import asyncio
import time
from typing import Optional

from neo4j import AsyncGraphDatabase
from neo4j.exceptions import ClientError

from graph.connection_context import get_override
from shared.config.app_settings import SETTINGS
from shared.events.runtime_logger import log_warning


LOCK_LABEL = "AnalysisLock"
LOCK_ID = "singleton"
CONSTRAINT_NAME = "analysis_lock_id_unique"

#: 락이 저절로 풀리는 시간. analyzer 가 죽어도 이만큼 뒤에는 다음 사람이 들어온다.
TTL_SECONDS = 180
#: 심장박동 주기. TTL 의 1/3 보다 짧아야 한 번 실패해도 락이 풀리지 않는다.
HEARTBEAT_SECONDS = 45

#: 라벨 저장소 DDL 경합을 다시 시도하는 횟수·간격.
_DDL_RETRIES = 4
_DDL_BACKOFF_SECONDS = 0.25

#: PostgreSQL 내부 카탈로그 경합의 흔적. **락 충돌이 아니다.**
_TRANSIENT_HINTS = (
    "pg_type",
    "pg_class",
    "pg_namespace",
    "tuple concurrently updated",
    "tuple concurrently deleted",
    "deadlock detected",
)


class AnalysisLockBusy(RuntimeError):
    """다른 실행이 이 분석 graph 를 이미 쓰고 있다."""

    def __init__(self, database: str, holder: Optional[str], started_at: Optional[float]):
        self.database = database
        self.holder = holder
        self.started_at = started_at
        who = holder or "다른 실행"
        super().__init__(
            f"{who} 이(가) 분석 graph '{database}' 를 쓰고 있습니다. "
            "분석은 대상 graph 를 덮어쓰므로 동시에 돌릴 수 없습니다. "
            "끝난 뒤 다시 시작하세요."
        )


def _is_lock_conflict(error: ClientError) -> bool:
    """**내 락 제약** 위반인가.

    "duplicate key" 만 보면 안 된다(실측). 새 라벨의 저장소를 두 세션이 동시에
    만들 때 게이트웨이는 PostgreSQL 내부 카탈로그 충돌
    ``duplicate key value violates unique constraint "pg_type_typname_nsp_index"``
    을 그대로 올려 보낸다. 그걸 락 충돌로 읽으면 **아무도 안 잡은 락을 잡혔다고
    보고**하게 된다. 그래서 내 제약 이름이 메시지에 있는지까지 본다 — 게이트웨이는
    PostgreSQL 제약 이름을 ``uq_<typeid>_<이름>`` 으로 만든다.
    """
    text = str(error).lower()
    return "duplicate key" in text and CONSTRAINT_NAME.lower() in text


def _is_transient(error: Exception) -> bool:
    """라벨 저장소를 만드는 DDL 경합인가 — 다시 시도하면 풀린다."""
    text = str(error).lower()
    return any(hint in text for hint in _TRANSIENT_HINTS)


def _target() -> tuple[str, str, str, str]:
    """(uri, user, password, database) — ``Neo4jClient`` 와 같은 진실을 읽는다."""
    override = get_override()
    if override is not None:
        return (
            override.uri,
            override.user,
            override.password,
            override.database or SETTINGS.graph_database.database,
        )
    db = SETTINGS.graph_database
    return db.uri, db.user, db.password, db.database


class AnalysisLock:
    """대상 분석 graph 에 대한 배타 락. ``async with`` 로 쓴다."""

    def __init__(self, run_id: str, holder: str):
        self._run_id = run_id
        self._holder = holder
        uri, user, password, database = _target()
        self._database = database
        self._driver = AsyncGraphDatabase.driver(uri, auth=(user, password))
        self._heartbeat: Optional[asyncio.Task] = None

    @property
    def database(self) -> str:
        return self._database

    async def acquire(self) -> "AnalysisLock":
        """락을 잡는다. 이미 잡혀 있으면 ``AnalysisLockBusy``.

        ``async with`` 이 아니라 별도 메서드로 열어 둔 이유가 있다. 분석은
        background task 로 돌고 응답은 즉시 스트림으로 나간다. 락을 그 task 안에서
        잡으면 **거절을 HTTP 상태로 알릴 수 없다** — 200 스트림이 먼저 나가고
        스트림 안에서 오류가 나므로, 화면은 "분석이 시작됐다" 를 먼저 보게 된다.
        그래서 잡기는 요청 처리 중에, 풀기는 task 끝에서 한다.
        """
        await self._ensure_constraint()
        if not await self._create():
            # 만료된 락이면 걷어내고 **한 번만** 다시 시도한다. 여기서 여러 번
            # 재시도하면 두 사람이 번갈아 서로의 락을 걷어내게 된다.
            holder, started_at, reclaimed = await self._reclaim_if_expired()
            if not reclaimed or not await self._create():
                await self._driver.close()
                raise AnalysisLockBusy(self._database, holder, started_at)
        self._heartbeat = asyncio.create_task(self._beat())
        return self

    async def release(self) -> None:
        if self._heartbeat is not None:
            self._heartbeat.cancel()
            try:
                await self._heartbeat
            except asyncio.CancelledError:
                pass
            self._heartbeat = None
        try:
            await self._release()
        finally:
            await self._driver.close()

    async def __aenter__(self) -> "AnalysisLock":
        return await self.acquire()

    async def __aexit__(self, *_exc) -> None:
        await self.release()

    async def _retrying_ddl(self, what: str, run) -> None:
        """라벨 저장소 DDL 경합만 다시 시도한다. 다른 오류는 그대로 올린다.

        이 graph 에 락 라벨이 처음 생기는 순간 두 PC 가 동시에 들어오면, 게이트웨이가
        PostgreSQL 타입·테이블을 각각 만들려다 부딪친다(실측). 한 번 만들어지면
        다시 나지 않는 경합이라 짧게 물러나 다시 하면 된다.
        """
        for attempt in range(_DDL_RETRIES):
            try:
                await run()
                return
            except Exception as error:  # noqa: BLE001 — 아래에서 종류를 가른다
                if not _is_transient(error) or attempt == _DDL_RETRIES - 1:
                    raise
                log_warning(
                    "graph.analysis_lock",
                    "ddl_retry",
                    f"락 저장소 경합으로 {what} 를 다시 시도한다",
                    database=self._database,
                    attempt=attempt + 1,
                    error=f"{type(error).__name__}: {str(error)[:120]}",
                )
                await asyncio.sleep(_DDL_BACKOFF_SECONDS * (attempt + 1))

    async def _ensure_constraint(self) -> None:
        async def _run() -> None:
            async with self._driver.session(database=self._database) as session:
                result = await session.run(
                    f"CREATE CONSTRAINT {CONSTRAINT_NAME} IF NOT EXISTS "
                    f"FOR (l:{LOCK_LABEL}) REQUIRE l.id IS UNIQUE"
                )
                await result.consume()

        await self._retrying_ddl("제약 생성", _run)

    async def _create(self) -> bool:
        """락 노드를 만든다. 이미 있으면 ``False``(예외를 내지 않는다)."""
        conflicted = False

        async def _run() -> None:
            nonlocal conflicted
            now = time.time()
            try:
                async with self._driver.session(database=self._database) as session:
                    result = await session.run(
                        f"CREATE (l:{LOCK_LABEL} {{id: $id, holder: $holder, runId: $run, "
                        f"startedAt: $now, expiresAt: $exp}})",
                        id=LOCK_ID,
                        holder=self._holder,
                        run=self._run_id,
                        now=now,
                        exp=now + TTL_SECONDS,
                    )
                    await result.consume()
            except ClientError as error:
                if _is_lock_conflict(error):
                    conflicted = True
                    return
                raise

        await self._retrying_ddl("락 획득", _run)
        return not conflicted

    async def _reclaim_if_expired(self) -> tuple[Optional[str], Optional[float], bool]:
        now = time.time()
        async with self._driver.session(database=self._database) as session:
            cursor = await session.run(
                f"MATCH (l:{LOCK_LABEL} {{id: $id}}) "
                "RETURN l.holder AS holder, l.startedAt AS startedAt, l.expiresAt AS expiresAt",
                id=LOCK_ID,
            )
            record = await cursor.single()
            if record is None:
                # 우리가 CREATE 에 실패한 뒤 상대가 정상 종료했다. 비었으니 다시 시도.
                return None, None, True
            expires_at = record["expiresAt"]
            if expires_at is not None and expires_at >= now:
                return record["holder"], record["startedAt"], False
            result = await session.run(
                f"MATCH (l:{LOCK_LABEL} {{id: $id}}) WHERE l.expiresAt < $now DETACH DELETE l",
                id=LOCK_ID,
                now=now,
            )
            await result.consume()
            log_warning(
                "graph.analysis_lock",
                "reclaimed_expired",
                "만료된 분석 락을 걷어냈다 — 앞 실행이 비정상 종료한 것으로 본다",
                database=self._database,
                previous_holder=str(record["holder"]),
            )
            return record["holder"], record["startedAt"], True

    async def _release(self) -> None:
        """**내 락만** 지운다. ``runId`` 를 조건에 넣지 않으면 남의 락을 풀 수 있다."""
        try:
            async with self._driver.session(database=self._database) as session:
                result = await session.run(
                    f"MATCH (l:{LOCK_LABEL} {{id: $id, runId: $run}}) DETACH DELETE l",
                    id=LOCK_ID,
                    run=self._run_id,
                )
                await result.consume()
        except Exception as error:  # noqa: BLE001 — 해제 실패로 분석 결과를 버리지 않는다
            log_warning(
                "graph.analysis_lock",
                "release_failed",
                "분석 락 해제에 실패했다 — TTL 로 저절로 풀린다",
                database=self._database,
                error=f"{type(error).__name__}: {error}",
            )

    async def _beat(self) -> None:
        while True:
            await asyncio.sleep(HEARTBEAT_SECONDS)
            try:
                async with self._driver.session(database=self._database) as session:
                    cursor = await session.run(
                        f"MATCH (l:{LOCK_LABEL} {{id: $id, runId: $run}}) "
                        "SET l.expiresAt = $exp RETURN count(l) AS n",
                        id=LOCK_ID,
                        run=self._run_id,
                        exp=time.time() + TTL_SECONDS,
                    )
                    record = await cursor.single()
                if record is None or not record["n"]:
                    # 우리 락이 사라졌다 — 누군가 만료로 걷어갔다는 뜻이다. 분석을
                    # 중단시키지는 않는다(여기서 죽이면 긴 실행이 버려진다). 다만
                    # **보호가 끊겼다는 사실은 남긴다.**
                    log_warning(
                        "graph.analysis_lock",
                        "lock_lost",
                        "분석 락이 사라졌다 — 이 실행은 더 이상 보호되지 않는다",
                        database=self._database,
                        run_id=self._run_id,
                    )
                    return
            except asyncio.CancelledError:
                raise
            except Exception as error:  # noqa: BLE001
                log_warning(
                    "graph.analysis_lock",
                    "heartbeat_failed",
                    "분석 락 갱신에 실패했다 — TTL 안에 회복되지 않으면 락이 풀린다",
                    database=self._database,
                    error=f"{type(error).__name__}: {error}",
                )
