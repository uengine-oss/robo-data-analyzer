"""Resolve and bind one atomically published Analyzer graph generation."""
from __future__ import annotations

from contextlib import contextmanager

from graph.generation import (
    ACTIVE_GENERATION_KEY,
    ACTIVE_GENERATION_LABEL,
    COMPLETED_STATUSES,
    GENERATION_LABEL,
)
from graph.ownership import ANALYZER_GRAPH_OWNER


class CompletedGenerationUnavailable(RuntimeError):
    """No active manifest points at a completed generation in this database."""


class GenerationScopedSession:
    """Neo4j session proxy that supplies the resolved generation parameter."""

    def __init__(self, session, generation_id: str):
        self._session = session
        self.generation_id = generation_id

    def run(self, query: str, **parameters):
        supplied_generation = parameters.get("generation_id")
        if supplied_generation is not None and supplied_generation != self.generation_id:
            raise CompletedGenerationUnavailable(
                "reader attempted to escape its pinned Analyzer graph generation"
            )
        parameters["generation_id"] = self.generation_id
        return self._session.run(query, **parameters)

    def __getattr__(self, name: str):
        return getattr(self._session, name)


@contextmanager
def completed_generation_session(
    driver,
    database: str,
    *,
    expected_generation_id: str | None = None,
):
    """Yield a session pinned to the active completed generation or fail closed."""
    with driver.session(database=database) as session:
        rows = session.run(
            f"""
MATCH (active:{ACTIVE_GENERATION_LABEL} {{
  graph_owner: $graph_owner,
  key: $active_key
}})
MATCH (generation:{GENERATION_LABEL} {{
  graph_owner: $graph_owner,
  run_id: active.current_generation_id
}})
WHERE generation.status IN $completed_statuses
RETURN generation.run_id AS generation_id, generation.status AS status
""",
            graph_owner=ANALYZER_GRAPH_OWNER,
            active_key=ACTIVE_GENERATION_KEY,
            completed_statuses=list(COMPLETED_STATUSES),
        ).data()
        if len(rows) != 1 or not rows[0]["generation_id"]:
            raise CompletedGenerationUnavailable(
                "완료되어 공개된 Analyzer graph generation이 없습니다"
            )
        row = rows[0]
        generation_id = str(row["generation_id"])
        if expected_generation_id is not None and generation_id != expected_generation_id:
            raise CompletedGenerationUnavailable(
                "검색 중 active graph generation이 변경되었습니다; 요청을 다시 시도하세요"
            )
        yield GenerationScopedSession(session, generation_id)
