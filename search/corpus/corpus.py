"""search/corpus — 요청 DB별 코퍼스 캐시 (전역 싱글턴 .npz 대체).

놀이터는 기동 시 한 DB 의 코퍼스·임베딩을 전역 캐시(.npz)해 요청별 DB 전환이 불가했다.
여기선 **(uri, database) 키 캐시**로 바꿔 요청마다 그 DB 의 코퍼스를 쓴다(전역 싱글턴 없음).
캐시는 임베딩 지문 + EMBEDDED 노드 수 서명으로 무효화한다(재분석 시 자동 재로드).

- bm25·structural 재료(코드 텍스트)는 인메모리 코퍼스(게이트 재현 우선 — fulltext 이관은 후속 스펙).
- dense 는 벡터 인덱스(recall) — 코퍼스는 벡터를 안 든다(scan 원복 모드일 때만 로드).
- 모델 일치 불변식(FR-006): 그래프 지문 ≠ 질의 임베더 지문이면 거부. EMBED_META 없으면 임베딩 없음.
"""
from __future__ import annotations

from search.corpus.embed_material import EMBED_META_LABEL, EMBEDDED_LABEL
from search.ranking.recall import tokenize
from shared.config.settings import SETTINGS
from llm import embedder
from graph.ownership import ANALYZER_GRAPH_OWNER

_NO_EMBED_MSG = "이 그래프에는 검색 임베딩이 없습니다 (분석을 다시 실행하세요)"

_CORPUS_QUERY = f"""
MATCH (n)
WHERE n.graph_owner = '{ANALYZER_GRAPH_OWNER}'
  AND n.summary IS NOT NULL AND n.summary <> ''
  AND NOT n:FILE AND NOT n:MODULE AND NOT n:PACKAGE
RETURN n.id AS id, coalesce(n.type, head(labels(n))) AS label,
       coalesce(n.name,'') AS name, n.summary AS summary,
       coalesce(n.code_text,'') AS code_text,
       coalesce(n.search_text,'') AS search_text,
       coalesce(n.stereotype,'') AS stereotype,
       coalesce(n.logical_name,'') AS logical_name
ORDER BY id
"""
_FAN_IN_QUERY = (f"MATCH (a {{graph_owner: '{ANALYZER_GRAPH_OWNER}'}})-[:CALLS]->"
                 f"(n {{graph_owner: '{ANALYZER_GRAPH_OWNER}'}}) WHERE n.id IS NOT NULL "
                 "RETURN n.id AS id, count(DISTINCT a) AS fi")

# (uri, db) -> (signature, Corpus)
_cache: dict[tuple[str, str], tuple[tuple, "Corpus"]] = {}


class SearchUnavailable(RuntimeError):
    """검색 불가(임베딩 없음·모델 불일치) — 라우터가 error 이벤트로 표면화(빈 성공 위장 금지)."""


class Corpus:
    """요청 DB 한 벌의 코퍼스 — bm25/structural 재료 + by_id 메타 + fan_in (+scan 벡터)."""

    def __init__(self, rows: list[dict], fan_in: dict, vecs=None):
        self.rows = rows
        self.by_id = {r["id"]: r for r in rows}
        self.ids = [r["id"] for r in rows]
        self.fan_in = fan_in
        self._vecs = vecs
        from rank_bm25 import BM25Okapi
        texts = [f"{r['name']} {r.get('logical_name', '')} {r['summary']} {r.get('code_text', '')}"
                 for r in rows]
        self.bm25 = BM25Okapi([tokenize(t) for t in texts])

    def vectors(self):
        """scan 원복 모드의 전량 벡터 행렬(ids 정렬). index 모드면 None."""
        return self._vecs


def load_corpus() -> Corpus:
    """요청 DB 코퍼스(캐시). 지문 대조 후 서명 일치 시 재사용, 아니면 재로드."""
    from search.corpus.neo4j_reader import read_driver, resolve_target

    uri = resolve_target()[0]
    drv, db = read_driver()
    try:
        with drv.session(database=db) as s:
            meta = s.run(
                f"MATCH (m:{EMBED_META_LABEL} {{graph_owner: $graph_owner}}) "
                "RETURN m.fingerprint AS fp",
                graph_owner=ANALYZER_GRAPH_OWNER,
            ).single()
            fp = meta["fp"] if meta else None
            if not fp:
                raise SearchUnavailable(_NO_EMBED_MSG)
            want = embedder.fingerprint()
            if fp != want:
                raise SearchUnavailable(f"임베딩 모델 불일치 (그래프={fp}, 질의={want}) — 재분석 필요")
            count = s.run(
                f"MATCH (n:{EMBEDDED_LABEL} {{graph_owner: $graph_owner}}) RETURN count(n) AS c",
                graph_owner=ANALYZER_GRAPH_OWNER,
            ).single()["c"]
            if count == 0:
                raise SearchUnavailable(_NO_EMBED_MSG)

            sig = (fp, count)
            key = (uri, db)
            cached = _cache.get(key)
            if cached is not None and cached[0] == sig:
                return cached[1]

            rows = s.run(_CORPUS_QUERY).data()
            fan_in = {r["id"]: r["fi"] for r in s.run(_FAN_IN_QUERY).data()}
            vecs = _load_vectors(s, [r["id"] for r in rows]) if SETTINGS.semantic_search.dense_impl == "scan" else None
        corpus = Corpus(rows, fan_in, vecs)
        _cache[key] = (sig, corpus)
        return corpus
    finally:
        drv.close()


def _load_vectors(session, ids: list[str]):
    """scan 원복용 전량 벡터(ids 정렬). 인덱스 모드에선 호출 안 됨."""
    import numpy as np

    m = {r["id"]: r["v"] for r in session.run(
        "MATCH (n {graph_owner: $graph_owner}) "
        "WHERE n.id IN $ids AND n.embedding IS NOT NULL "
        "RETURN n.id AS id, n.embedding AS v",
        ids=ids,
        graph_owner=ANALYZER_GRAPH_OWNER,
    ).data()}
    return np.array([m[i] for i in ids if i in m], dtype=np.float32)
