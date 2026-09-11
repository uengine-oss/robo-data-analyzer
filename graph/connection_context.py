"""요청별 Neo4j 연결 override — Electron 호스트가 헤더로 전달한 연결을 run 컨텍스트에 싣는다.

설계 원칙:
- 진실의 기본값은 ``SETTINGS.graph_database``(.env). 브라우저/테스트/CLI 는 이 기본값을 그대로 쓴다.
- Electron 모드에서는 데스크톱이 고른 연결이 ``X-Neo4j-*`` 헤더로 들어온다.
  ``/analyze`` 엔드포인트가 이를 읽어 ``set_override`` 로 **contextvar** 에 싣고,
  ``asyncio.create_task`` 가 그 컨텍스트를 복사하므로 파이프라인 전 단계의
  ``Neo4jClient`` 가 자동으로 그 값을 본다(단계마다 인자 threading 불필요).
- contextvar 는 요청별 격리 — run 간 오염 없음. 미설정 시 ``None`` → SETTINGS 폴백.
"""
from __future__ import annotations

import contextvars
from dataclasses import dataclass
from typing import Mapping, Optional
from urllib.parse import urlsplit

from shared.config.app_settings import SETTINGS


@dataclass(frozen=True)
class Neo4jOverride:
    """요청이 실어 보낸 Neo4j 연결. ``database`` 미지정이면 호출측이 SETTINGS 기본 사용."""

    uri: str
    user: str
    password: str
    database: Optional[str] = None

    def __post_init__(self) -> None:
        parsed = urlsplit(self.uri)
        if parsed.scheme not in {
            "bolt", "bolt+s", "bolt+ssc", "neo4j", "neo4j+s", "neo4j+ssc"
        } or not parsed.hostname or parsed.username or parsed.password or len(self.uri) > 2048:
            raise ValueError("invalid Neo4j URI")
        if self.database and self.database.lower() == "system":
            raise ValueError("Neo4j system database is forbidden")

    @classmethod
    def from_headers(cls, headers: Mapping[str, str]) -> Optional["Neo4jOverride"]:
        """``X-Neo4j-*`` 헤더 → override. URI 없으면(=브라우저/테스트) None → SETTINGS 폴백.

        Starlette ``request.headers`` 는 대소문자 무시 — 소문자 키로 조회한다.
        """
        uri = headers.get("x-neo4j-uri")
        if not uri:
            # **대상 graph 만 정해 주는 호출자가 있다.** 브라우저에서 도는 호스트는
            # 연결 자격을 클라이언트로 내보내지 않으므로 URI·비밀번호를 실을 수
            # 없고, 프로젝트마다 분석 graph 를 가르기 위해 `X-Neo4j-Database` 만
            # 보낸다.
            #
            # 여기서 `None` 을 돌려주면 그 run 이 **`.env` 의 공용 graph** 로 가고,
            # 매 run 이 대상을 통째로 비우므로 다른 프로젝트의 분석이 사라진다.
            # 오류는 안 난다 — 프로젝트 분리가 조용히 풀린다.
            database = headers.get("x-neo4j-database") or None
            if not database:
                return None
            if database.lower() == "system":
                raise ValueError("Neo4j system database is forbidden")
            fallback = SETTINGS.graph_database
            return cls(uri=fallback.uri, user=fallback.user,
                       password=fallback.password, database=database)
        parsed = urlsplit(uri)
        if parsed.scheme not in {
            "bolt", "bolt+s", "bolt+ssc", "neo4j", "neo4j+s", "neo4j+ssc"
        }:
            raise ValueError("unsupported Neo4j URI scheme")
        if not parsed.hostname or parsed.username or parsed.password or len(uri) > 2048:
            raise ValueError("invalid Neo4j URI")
        database = headers.get("x-neo4j-database") or None
        if database and database.lower() == "system":
            raise ValueError("Neo4j system database is forbidden")
        return cls(
            uri=uri,
            user=headers.get("x-neo4j-user", "neo4j"),
            password=headers.get("x-neo4j-password", ""),
            database=database,
        )


_neo4j_override: contextvars.ContextVar[Optional[Neo4jOverride]] = contextvars.ContextVar(
    "neo4j_override", default=None
)


def set_override(conn: Optional[Neo4jOverride]) -> None:
    """현재 run 컨텍스트의 Neo4j override 설정 (요청 경계에서 1회)."""
    _neo4j_override.set(conn)


def get_override() -> Optional[Neo4jOverride]:
    """현재 컨텍스트의 Neo4j override 조회 (Neo4jClient 가 사용). 없으면 None → SETTINGS."""
    return _neo4j_override.get()
