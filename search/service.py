"""search/service — 검색 실행 조립 단일 진입점 (REST 라우터·MCP 도구 공용).

코퍼스 로드 → 요청 DB 드라이버 → Retriever → 뭉치 포장. 이 배관이 REST 와 MCP 에
사본으로 갈라지면 한쪽만 고치는 사고가 나므로 여기 한 곳에 둔다(SC-005 로직 단일).
sync — 호출측(async)이 to_thread 로 격리한다.
"""
from __future__ import annotations

from dataclasses import replace

from search.config import DEFAULT
from search.corpus.corpus import load_corpus
from search.corpus.neo4j_reader import read_driver
from search.corpus.generation import completed_generation_session
from search.ranking.retriever import Retriever


def run_search(
    query: str,
    on_progress=None,
    *,
    top_n: int | None = None,
    analysis_scope_id: str | None = None,
) -> tuple[list[dict], dict]:
    """요구사항 1건 검색 → (clusters, meta). SearchUnavailable 은 호출측이 표면화."""
    if top_n is not None and not 1 <= top_n <= 100:
        raise ValueError(f"top_n must be between 1 and 100: {top_n!r}")
    cfg = replace(DEFAULT, cluster_top_n=top_n) if top_n is not None else DEFAULT
    meta: dict = {}
    drv, db = read_driver()
    try:
        with completed_generation_session(
            drv, db, analysis_scope_id=analysis_scope_id,
        ) as session:
            corpus = load_corpus(
                analysis_scope_id=session.analysis_scope_id,
                generation_id=session.generation_id,
            )
            nodes = Retriever(session, corpus).retrieve(
                query, cfg, on_progress=on_progress, meta=meta)
    finally:
        drv.close()

    meta["coverage"] = {"corpus_nodes": len(corpus.ids)}
    meta["generation_id"] = corpus.generation_id
    meta["analysis_scope_id"] = session.analysis_scope_id
    meta["quality_status"] = session.quality_status
    meta["call_resolution"] = session.call_resolution
    meta["import_resolution"] = session.import_resolution
    if not nodes:
        return [], meta
    score = max(n["relevance"] for n in nodes)
    return [{"score": round(score, 3), "nodes": nodes}], meta
