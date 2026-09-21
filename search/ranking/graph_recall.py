"""Dense·BM25·구조 채널 후보 회수와 RRF 융합.

Dense 채널은 메모리 전량 코사인(`vecs @ qv`) 대신 routine label별 벡터
인덱스를 직접 조회한다.
BM25·structural 채널은 요청 DB 코퍼스(corpus.Corpus)를 쓴다.
전역 싱글턴 없음 — session·corpus 는 요청마다 주입된다.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

import numpy as np

from shared.config.app_settings import SETTINGS

_TOKEN = re.compile(r"[A-Za-z0-9]+|[가-힣]+")

def tokenize(text: str) -> list[str]:
    """소문자 영숫자/한글 토큰 + 식별자 snake/camel 분해 + 한글 2·3-gram(형태소 대용)."""
    out: list[str] = []
    for m in _TOKEN.findall(text or ""):
        t = m.lower()
        out.append(t)
        for part in re.split(r"_|(?<=[a-z])(?=[A-Z])", m):
            p = part.lower()
            if p and p != t:
                out.append(p)
        if len(t) >= 2 and any("가" <= ch <= "힣" for ch in t):
            for n in (2, 3):
                for i in range(len(t) - n + 1):
                    gram = t[i:i + n]
                    if all("가" <= ch <= "힣" for ch in gram):
                        out.append(gram)
    return out


@dataclass
class Cand:
    node: dict
    rrf: float = 0.0
    channels: dict = field(default_factory=dict)
    dense: float = 0.0
    bm25: float = 0.0


def _dense_index(session, corpus, qv, k: int) -> list[tuple[str, float]]:
    """요청 DB 벡터 인덱스 근사탐색 top-k (id, cosine)."""
    found: dict[str, float] = {}
    for label in corpus.routine_labels:
        rows = session.run(
            f"MATCH (node:{label}) "
            f"SEARCH node IN (VECTOR INDEX {label.lower()}_search_embedding "
            "FOR $qv LIMIT $k) SCORE AS score "
            "WHERE node._owner = 'analyzer' "
            "RETURN node.product_id AS id, score ORDER BY score DESC",
            k=k,
            qv=[float(x) for x in qv],
        ).data()
        for row in rows:
            node_id = str(row.get("id") or "")
            if node_id:
                found[node_id] = max(found.get(node_id, -1.0), float(row["score"]))
    return sorted(found.items(), key=lambda item: -item[1])[:k]


def _dense_scan(corpus, qv, k: int) -> list[tuple[str, float]]:
    """SC-002 원복 경로 — 전량 코사인. 인덱스와 한 곳(dense_impl)에서 교체된다."""
    vecs = corpus.vectors()
    if vecs is None or not len(corpus.ids):
        return []
    scores = vecs @ qv
    order = np.argsort(-scores)[:k]
    return [(corpus.ids[i], float(scores[i])) for i in order]


def _sparse(corpus, query: str, k: int) -> list[tuple[str, float]]:
    scores = corpus.bm25.get_scores(tokenize(query))
    order = np.argsort(-scores)[:k]
    return [(corpus.ids[i], float(scores[i])) for i in order if scores[i] > 0]


def _structural(corpus, query: str, k: int) -> list[tuple[str, float]]:
    toks = set(t for t in tokenize(query) if len(t) >= 2)
    hits: list[tuple[str, float]] = []
    for c in corpus.rows:
        summary = (c.get("summary") or "").lower()
        sc = sum(1 for t in toks if t in summary)
        if sc > 0:
            hits.append((c["id"], float(sc)))
    hits.sort(key=lambda x: -x[1])
    return hits[:k]


def recall(session, corpus, queries: list[str], query_vecs: list, cfg) -> list[Cand]:
    """채널×질의 전부 RRF 융합. dense=인덱스(기본)/scan(원복), bm25·structural=코퍼스."""
    rrf_k = cfg.rrf_k
    agg: dict[str, Cand] = {}
    use_index = SETTINGS.semantic_search.dense_impl != "scan"

    def add(ch: str, ranked: list[tuple[str, float]]):
        for rank, (cid, sc) in enumerate(ranked):
            if cid not in corpus.by_id:
                continue
            c = agg.get(cid) or Cand(node=corpus.by_id[cid])
            c.rrf += 1.0 / (rrf_k + rank + 1)
            if ch not in c.channels or rank + 1 < c.channels[ch]:
                c.channels[ch] = rank + 1
            if ch == "dense":
                c.dense = max(c.dense, sc)
            elif ch == "bm25":
                c.bm25 = max(c.bm25, sc)
            agg[cid] = c

    for q, qv in zip(queries, query_vecs):
        if cfg.use_dense:
            dense = _dense_index(session, corpus, qv, cfg.fetch_k) if use_index else _dense_scan(corpus, qv, cfg.fetch_k)
            add("dense", dense)
        if cfg.use_bm25:
            add("bm25", _sparse(corpus, q, cfg.fetch_k))
        if cfg.use_structural:
            add("structural", _structural(corpus, q, cfg.fetch_k))

    return sorted(agg.values(), key=lambda c: -c.rrf)
