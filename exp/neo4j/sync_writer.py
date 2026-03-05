"""동기 Neo4j writer. Step2/Step3에서 그때그때 UNWIND 배치 저장용."""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

DEFAULT_BATCH_SIZE = 40


def _create_sync_driver():
    """동기 neo4j 드라이버 (sync API)."""
    from neo4j import GraphDatabase
    uri = os.environ.get("NEO4J_URI", "bolt://127.0.0.1:7687")
    user = os.environ.get("NEO4J_USER", "neo4j")
    password = os.environ.get("NEO4J_PASSWORD", "neo4j")
    return GraphDatabase.driver(uri, auth=(user, password))


def is_neo4j_configured() -> bool:
    """NEO4J_URI가 설정되어 있으면 True (그때그때 저장 사용)."""
    return bool(os.environ.get("NEO4J_URI", "").strip())


class SyncNeo4jWriter:
    """동기 Neo4j 배치 기록. UNWIND 한 번에 N건 실행."""

    def __init__(self, database: Optional[str] = None):
        self._driver = None
        self._database = database or os.environ.get("NEO4J_DATABASE", "neo4j")

    def connect(self) -> None:
        if self._driver is None:
            self._driver = _create_sync_driver()

    def close(self) -> None:
        if self._driver:
            try:
                self._driver.close()
            except Exception:
                pass
            self._driver = None

    def run_one(self, query: str, params: Optional[Dict[str, Any]] = None) -> None:
        """단일 쿼리 실행 (UNWIND 배치용)."""
        if not query or not query.strip():
            return
        self.connect()
        try:
            with self._driver.session(database=self._database) as session:
                session.run(query, params or {})
        except Exception as e:
            logger.warning("[EXP:NEO4J] UNWIND 배치 실행 실패: %s", e)
            raise

    def run_unwind_batch(self, query_and_params: Dict[str, Any]) -> None:
        """{"query": "...", "params": {"rows": [...]}} 형태로 한 번 실행."""
        q = query_and_params.get("query", "").strip()
        p = query_and_params.get("params") or {}
        if not q:
            return
        self.run_one(q, p)
