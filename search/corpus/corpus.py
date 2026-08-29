"""Load one completed generation's current SearchSegment-backed corpus.

The cache is keyed by database and analysis scope.  Its signature comes directly
from the published SearchSegment population, so search never depends on legacy
marker labels or a second embedding metadata node.
"""
from __future__ import annotations

from llm import embedder
from search.ranking.recall import tokenize
from shared.config.settings import SETTINGS
from search.corpus.generation import (
    CompletedGenerationUnavailable,
    completed_generation_session,
)


_NO_EMBED_MSG = "이 그래프에는 검색 segment가 없습니다 (분석을 다시 실행하세요)"

_CORPUS_QUERY = """
MATCH (node:CodeNode {
  analysis_scope_id: $analysis_scope_id,
  generation_id: $generation_id
})-[:HAS_SEARCH_SEGMENT]->(segment:SearchSegment {
  analysis_scope_id: $analysis_scope_id,
  generation_id: $generation_id
})
WHERE node.summary IS NOT NULL AND node.summary <> ''
RETURN node.stable_id AS id,
       node.code_kind AS label,
       coalesce(node.name,'') AS name,
       node.summary AS summary,
       coalesce(node.stereotype,'') AS stereotype,
       segment.stable_id AS search_segment_id,
       segment.embedding_model_id AS embedding_model_id
ORDER BY id
"""

_FAN_IN_QUERY = """
MATCH (caller:CodeNode {
  analysis_scope_id: $analysis_scope_id,
  generation_id: $generation_id
})-[:CALLS]->(callee:CodeNode {
  analysis_scope_id: $analysis_scope_id,
  generation_id: $generation_id
})
RETURN callee.stable_id AS id, count(DISTINCT caller) AS fi
"""

_SEGMENT_SIGNATURE_QUERY = """
MATCH (segment:SearchSegment {
  analysis_scope_id: $analysis_scope_id,
  generation_id: $generation_id
})
RETURN count(segment) AS segment_count,
       count(segment.embedding) AS embedded_count,
       collect(DISTINCT segment.embedding_model_id) AS model_ids,
       collect(DISTINCT size(segment.embedding)) AS dimensions
"""

_SEGMENT_VECTORS_QUERY = """
MATCH (node:CodeNode {
  analysis_scope_id: $analysis_scope_id,
  generation_id: $generation_id
})-[:HAS_SEARCH_SEGMENT]->(segment:SearchSegment {
  analysis_scope_id: $analysis_scope_id,
  generation_id: $generation_id
})
WHERE node.stable_id IN $ids
RETURN node.stable_id AS id, segment.embedding AS vector
ORDER BY id
"""


_cache: dict[tuple[str, str, str], tuple[tuple, "Corpus"]] = {}


class SearchUnavailable(RuntimeError):
    """The selected completed generation cannot provide a complete search corpus."""


class Corpus:
    """BM25, structural and optional scan vectors for one exact generation."""

    def __init__(
        self,
        rows: list[dict],
        fan_in: dict,
        analysis_scope_id: str,
        generation_id: str,
        vecs=None,
    ):
        ids = [str(row.get("id") or "") for row in rows]
        if any(not node_id for node_id in ids) or len(ids) != len(set(ids)):
            raise SearchUnavailable("검색 corpus의 CodeNode identity가 비어 있거나 중복됩니다")
        self.rows = rows
        self.by_id = {row["id"]: row for row in rows}
        self.ids = ids
        self.fan_in = fan_in
        self.analysis_scope_id = analysis_scope_id
        self.generation_id = generation_id
        self._vecs = vecs
        from rank_bm25 import BM25Okapi

        texts = [(row.get("summary") or "").strip() for row in rows]
        self.bm25 = BM25Okapi([tokenize(text) for text in texts])

    def vectors(self):
        """Return generation-ordered vectors only for the explicit scan mode."""
        return self._vecs


def _validated_segment_signature(session) -> tuple[str, int, int]:
    row = session.run(_SEGMENT_SIGNATURE_QUERY).single()
    segment_count = int(row["segment_count"] if row else 0)
    embedded_count = int(row["embedded_count"] if row else 0)
    model_ids = sorted(str(value) for value in (row["model_ids"] if row else []) if value)
    dimensions = sorted(int(value) for value in (row["dimensions"] if row else []) if value)
    if segment_count == 0:
        raise SearchUnavailable(_NO_EMBED_MSG)
    if embedded_count != segment_count:
        raise SearchUnavailable(
            "검색 segment embedding이 누락되었습니다: "
            f"segments={segment_count}, embeddings={embedded_count}"
        )
    if len(model_ids) != 1 or len(dimensions) != 1:
        raise SearchUnavailable(
            "한 generation의 검색 segment는 하나의 embedding model과 차원을 사용해야 합니다"
        )
    expected_model = embedder.fingerprint()
    if model_ids[0] != expected_model:
        raise SearchUnavailable(
            f"임베딩 모델 불일치 (그래프={model_ids[0]}, 질의={expected_model}) — 재분석 필요"
        )
    return model_ids[0], dimensions[0], segment_count


def load_corpus(
    *,
    analysis_scope_id: str | None = None,
    generation_id: str | None = None,
) -> Corpus:
    """Load and cache one active completed generation without compatibility reads."""
    from search.corpus.neo4j_reader import read_driver, resolve_target

    uri = resolve_target()[0]
    driver, database = read_driver()
    try:
        with completed_generation_session(
            driver,
            database,
            analysis_scope_id=analysis_scope_id,
            expected_generation_id=generation_id,
        ) as session:
            analysis_scope_id = session.analysis_scope_id
            generation_id = session.generation_id
            model_id, dimension, segment_count = _validated_segment_signature(session)
            signature = (
                analysis_scope_id,
                generation_id,
                model_id,
                dimension,
                segment_count,
            )
            key = (uri, database, analysis_scope_id)
            cached = _cache.get(key)
            if cached is not None and cached[0] == signature:
                return cached[1]

            rows = session.run(_CORPUS_QUERY).data()
            if len(rows) != segment_count:
                raise SearchUnavailable(
                    "SearchSegment와 검색 CodeNode의 exact-once 연결이 닫히지 않았습니다: "
                    f"segments={segment_count}, linked_nodes={len(rows)}"
                )
            fan_in = {
                row["id"]: row["fi"]
                for row in session.run(_FAN_IN_QUERY).data()
            }
            vectors = (
                _load_vectors(session, [row["id"] for row in rows])
                if SETTINGS.semantic_search.dense_impl == "scan"
                else None
            )
            corpus = Corpus(
                rows,
                fan_in,
                analysis_scope_id,
                generation_id,
                vectors,
            )
        _cache[key] = (signature, corpus)
        return corpus
    except CompletedGenerationUnavailable as error:
        raise SearchUnavailable(str(error)) from error
    finally:
        driver.close()


def _load_vectors(session, ids: list[str]):
    """Load exactly one SearchSegment vector for every corpus CodeNode."""
    import numpy as np

    rows = session.run(_SEGMENT_VECTORS_QUERY, ids=ids).data()
    vectors: dict[str, object] = {}
    for row in rows:
        node_id = str(row.get("id") or "")
        if not node_id or node_id in vectors:
            raise SearchUnavailable(
                f"CodeNode 검색 vector가 비어 있거나 중복됩니다: {node_id!r}"
            )
        vectors[node_id] = row.get("vector")
    missing = [node_id for node_id in ids if node_id not in vectors]
    if missing:
        raise SearchUnavailable(
            f"CodeNode 검색 vector가 누락되었습니다: first={missing[0]!r}, count={len(missing)}"
        )
    return np.array([vectors[node_id] for node_id in ids], dtype=np.float32)
