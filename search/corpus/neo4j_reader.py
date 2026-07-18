"""요청별 데이터베이스를 읽는 검색 전용 동기 Neo4j 어댑터.

검색 코어는 놀이터에서 이식된 sync 로직이라 async ``Neo4jClient`` 대신 sync 드라이버를 쓴다.
단, DB 출처는 동일하게 **요청 컨텍스트의 override**(``get_override``)를 읽는다 — 라우터가
``set_override`` 로 실은 요청 DB 를 ``asyncio.to_thread`` 가 컨텍스트 복사로 전달하므로,
스레드 안에서 이 드라이버가 그 DB 를 본다(전역 싱글턴 없음, 요청별 DB 전환 공짜).
"""
from __future__ import annotations

from analyzer_settings import SETTINGS
from graph.connection_context import get_override


def resolve_target() -> tuple[str, str, str, str]:
    """(uri, user, password, database) — 요청 override 우선, 없으면 SETTINGS."""
    ov = get_override()
    if ov is not None:
        return ov.uri, ov.user, ov.password, (ov.database or SETTINGS.graph_database.database)
    return SETTINGS.graph_database.uri, SETTINGS.graph_database.user, SETTINGS.graph_database.password, SETTINGS.graph_database.database


def read_driver():
    """읽기 전용 sync 드라이버 + 대상 DB. 호출측이 close 책임. 캐시 키(uri,db)는 corpus 가 관리."""
    from neo4j import GraphDatabase

    uri, user, password, db = resolve_target()
    return GraphDatabase.driver(uri, auth=(user, password)), db
