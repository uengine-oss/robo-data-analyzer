"""Resolve and bind one atomically published Analyzer graph generation."""
from __future__ import annotations

from contextlib import contextmanager

from graph.generation import COMPLETED_STATUSES
from graph.ownership import ANALYZER_GRAPH_OWNER
from graph.publication_schema import (
    ANALYSIS_GENERATION_LABEL,
    ANALYSIS_SCOPE_LABEL,
)
from pipeline.models.call_resolution import validate_call_resolution_summary
from pipeline.models.import_resolution import validate_import_resolution_summary
import json


class CompletedGenerationUnavailable(RuntimeError):
    """No active manifest points at a completed generation in this database."""


class GenerationScopedSession:
    """Neo4j session proxy that supplies the resolved generation parameter."""

    def __init__(
        self, session, analysis_scope_id: str, generation_id: str, quality_status: str,
        call_resolution: dict, import_resolution: dict,
    ):
        self._session = session
        self.analysis_scope_id = analysis_scope_id
        self.generation_id = generation_id
        self.quality_status = quality_status
        self.call_resolution = call_resolution
        self.import_resolution = import_resolution

    def run(self, query: str, **parameters):
        supplied_generation = parameters.get("generation_id")
        if supplied_generation is not None and supplied_generation != self.generation_id:
            raise CompletedGenerationUnavailable(
                "reader attempted to escape its pinned Analyzer graph generation"
            )
        parameters["generation_id"] = self.generation_id
        supplied_scope = parameters.get("analysis_scope_id")
        if supplied_scope is not None and supplied_scope != self.analysis_scope_id:
            raise CompletedGenerationUnavailable(
                "reader attempted to escape its pinned Analyzer analysis scope"
            )
        parameters["analysis_scope_id"] = self.analysis_scope_id
        return self._session.run(query, **parameters)

    def __getattr__(self, name: str):
        return getattr(self._session, name)


@contextmanager
def completed_generation_session(
    driver,
    database: str,
    *,
    analysis_scope_id: str | None = None,
    expected_generation_id: str | None = None,
):
    """Yield a session pinned to the active completed generation or fail closed."""
    with driver.session(database=database) as session:
        rows = session.run(
            f"""
MATCH (active:{ANALYSIS_SCOPE_LABEL} {{
  graph_owner: $graph_owner
}})
WHERE $analysis_scope_id IS NULL
   OR active.analysis_scope_id = $analysis_scope_id
MATCH (generation:{ANALYSIS_GENERATION_LABEL} {{
  graph_owner: $graph_owner,
  analysis_scope_id: active.analysis_scope_id,
  generation_id: active.active_generation_id
}})
WHERE generation.generation_status IN $completed_statuses
RETURN active.analysis_scope_id AS analysis_scope_id,
       generation.generation_id AS generation_id,
       generation.generation_status AS status,
       generation.call_resolution_summary_json AS call_resolution_summary_json,
       generation.import_resolution_summary_json AS import_resolution_summary_json
""",
            graph_owner=ANALYZER_GRAPH_OWNER,
            analysis_scope_id=analysis_scope_id,
            completed_statuses=list(COMPLETED_STATUSES),
        ).data()
        if len(rows) != 1 or not rows[0]["generation_id"]:
            raise CompletedGenerationUnavailable(
                "완료되어 공개된 Analyzer graph generation이 없습니다"
            )
        row = rows[0]
        resolved_scope_id = str(row.get("analysis_scope_id") or "")
        if not resolved_scope_id:
            raise CompletedGenerationUnavailable(
                "완료 generation의 analysis scope ID가 없습니다"
            )
        generation_id = str(row["generation_id"])
        quality_status = str(row.get("status") or "")
        raw_summary = row.get("call_resolution_summary_json")
        if not isinstance(raw_summary, str) or not raw_summary:
            raise CompletedGenerationUnavailable(
                "완료 generation에 call resolution closure가 없습니다"
            )
        try:
            call_resolution = validate_call_resolution_summary(json.loads(raw_summary))
        except (json.JSONDecodeError, ValueError) as error:
            raise CompletedGenerationUnavailable(
                f"완료 generation의 call resolution closure가 유효하지 않습니다: {error}"
            ) from error
        raw_import_summary = row.get("import_resolution_summary_json")
        if not isinstance(raw_import_summary, str) or not raw_import_summary:
            raise CompletedGenerationUnavailable(
                "completed generation has no import resolution closure"
            )
        try:
            import_resolution = validate_import_resolution_summary(
                json.loads(raw_import_summary)
            )
        except (json.JSONDecodeError, ValueError) as error:
            raise CompletedGenerationUnavailable(
                f"completed generation import resolution closure is invalid: {error}"
            ) from error
        if expected_generation_id is not None and generation_id != expected_generation_id:
            raise CompletedGenerationUnavailable(
                "검색 중 active graph generation이 변경되었습니다; 요청을 다시 시도하세요"
            )
        yield GenerationScopedSession(
            session, resolved_scope_id, generation_id, quality_status,
            call_resolution, import_resolution,
        )
