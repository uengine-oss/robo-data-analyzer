"""Single search entrypoint shared by REST and MCP."""
from __future__ import annotations

from dataclasses import replace

from search.search_config import DEFAULT
from search.corpus.search_corpus import load_corpus
from search.corpus.neo4j_reader import read_driver
from search.ranking.graph_retriever import Retriever


def run_search(
    query: str,
    on_progress=None,
    *,
    top_n: int | None = None,
) -> tuple[list[dict], dict]:
    if top_n is not None and not 1 <= top_n <= 100:
        raise ValueError(f"top_n must be between 1 and 100: {top_n!r}")
    cfg = replace(DEFAULT, cluster_top_n=top_n) if top_n is not None else DEFAULT
    meta: dict = {}
    driver, database = read_driver()
    try:
        with driver.session(database=database) as session:
            corpus = load_corpus(session=session)
            nodes = Retriever(session, corpus).retrieve(
                query, cfg, on_progress=on_progress, meta=meta,
            )
    finally:
        driver.close()

    meta["coverage"] = {"corpus_nodes": len(corpus.ids)}
    if not nodes:
        return [], meta
    score = max(node["relevance"] for node in nodes)
    return [{"score": round(score, 3), "nodes": nodes}], meta
