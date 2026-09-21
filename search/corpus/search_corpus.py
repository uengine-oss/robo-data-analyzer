"""Load the current routine nodes used by semantic search."""
from __future__ import annotations

from pipeline.source_languages import ROUTINE_LABELS
from search.ranking.graph_recall import tokenize
from shared.config.app_settings import SETTINGS


_NO_EMBED_MSG = "검색 가능한 함수 임베딩이 없습니다. 분석을 다시 실행하세요."

_CORPUS_QUERY = """
MATCH (node)
WHERE node._owner = 'analyzer'
  AND any(label IN labels(node) WHERE label IN $routine_labels)
  AND node.summary IS NOT NULL AND node.summary <> ''
RETURN node.product_id AS id,
       head([label IN labels(node) WHERE label IN $routine_labels]) AS label,
       coalesce(node.name,'') AS name,
       node.summary AS summary,
       coalesce(node.stereotype,'') AS stereotype,
       node._search_embedding IS NOT NULL AS embedded,
       size(node._search_embedding) AS dimension
ORDER BY id
"""

_FAN_IN_QUERY = """
MATCH (caller)-[:CALLS]->(callee)
WHERE caller._owner = 'analyzer' AND callee._owner = 'analyzer'
  AND any(label IN labels(caller) WHERE label IN $routine_labels)
  AND any(label IN labels(callee) WHERE label IN $routine_labels)
RETURN callee.product_id AS id, count(DISTINCT caller) AS fi
"""

_VECTORS_QUERY = """
MATCH (node)
WHERE node._owner = 'analyzer' AND node.product_id IN $ids
  AND any(label IN labels(node) WHERE label IN $routine_labels)
RETURN node.product_id AS id, node._search_embedding AS vector
ORDER BY id
"""


class SearchUnavailable(RuntimeError):
    """The current Analyzer graph cannot provide a complete search corpus."""


class Corpus:
    def __init__(self, rows: list[dict], fan_in: dict, vecs=None):
        ids = [str(row.get("id") or "") for row in rows]
        if any(not node_id for node_id in ids) or len(ids) != len(set(ids)):
            raise SearchUnavailable("검색 함수 ID가 비어 있거나 중복됩니다.")
        self.rows = rows
        self.by_id = {row["id"]: row for row in rows}
        self.ids = ids
        self.fan_in = fan_in
        self.routine_labels = sorted({str(row["label"]) for row in rows})
        self._vecs = vecs
        from rank_bm25 import BM25Okapi

        self.bm25 = BM25Okapi([
            tokenize((row.get("summary") or "").strip()) for row in rows
        ])

    def vectors(self):
        return self._vecs


def load_corpus(*, session=None) -> Corpus:
    """Load the current product graph without scope or generation indirection."""
    if session is None:
        from search.corpus.neo4j_reader import read_driver

        driver, database = read_driver()
        try:
            with driver.session(database=database) as owned_session:
                return load_corpus(session=owned_session)
        finally:
            driver.close()

    rows = session.run(
        _CORPUS_QUERY, routine_labels=sorted(ROUTINE_LABELS),
    ).data()
    if not rows:
        raise SearchUnavailable(_NO_EMBED_MSG)
    missing = [row["id"] for row in rows if not row.get("embedded")]
    if missing:
        raise SearchUnavailable(
            f"함수 임베딩이 누락되었습니다: first={missing[0]!r}, count={len(missing)}"
        )
    dimensions = {int(row.get("dimension") or 0) for row in rows}
    if len(dimensions) != 1 or 0 in dimensions:
        raise SearchUnavailable("함수 임베딩 차원이 서로 다릅니다.")
    fan_in = {
        row["id"]: row["fi"]
        for row in session.run(
            _FAN_IN_QUERY, routine_labels=sorted(ROUTINE_LABELS),
        ).data()
    }
    vectors = (
        _load_vectors(session, [row["id"] for row in rows])
        if SETTINGS.semantic_search.dense_impl == "scan"
        else None
    )
    return Corpus(rows, fan_in, vectors)


def _load_vectors(session, ids: list[str]):
    import numpy as np

    rows = session.run(
        _VECTORS_QUERY,
        ids=ids,
        routine_labels=sorted(ROUTINE_LABELS),
    ).data()
    vectors = {str(row.get("id") or ""): row.get("vector") for row in rows}
    missing = [node_id for node_id in ids if not vectors.get(node_id)]
    if missing:
        raise SearchUnavailable(
            f"함수 검색 벡터가 누락되었습니다: first={missing[0]!r}, count={len(missing)}"
        )
    return np.array([vectors[node_id] for node_id in ids], dtype=np.float32)
