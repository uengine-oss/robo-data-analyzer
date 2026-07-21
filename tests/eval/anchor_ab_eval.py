"""spec 053 — legacy cluster와 global anchor/evidence를 같은 골드로 비교한다.

점수는 정답확률이 아니라 exact function-name recall이다. anchor 적중과 1홉 evidence
구제를 분리해, 상위 3개 절단이 무엇을 잃는지 숨기지 않는다.
"""
from __future__ import annotations

import argparse
import sys
import time
from dataclasses import replace
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT))

from search.config import DEFAULT
from search.corpus.corpus import load_corpus
from tests.eval.gate_eval import load_items
from search.corpus.neo4j_reader import read_driver
from search.ranking.retriever import Retriever
from shared.config.settings import SETTINGS
from graph.connection_context import Neo4jOverride, set_override


def run_db(db: str, pool_k: int, wanted_ids: set[str] | None, evidence_top_n: int) -> dict:
    set_override(Neo4jOverride(SETTINGS.graph_database.uri, SETTINGS.graph_database.user, SETTINGS.graph_database.password, db))
    corpus = load_corpus()
    drv, dbn = read_driver()
    legacy_cfg = replace(DEFAULT, cluster_selection_mode="legacy")
    global_cfg = replace(
        DEFAULT,
        cluster_selection_mode="global_anchor",
        cluster_judge_mode="off",
        cluster_evidence_top_n=evidence_top_n,
    )
    totals = {
        "gold": 0, "legacy": 0, "global": 0, "anchor": 0,
        "legacy_size": 0, "global_size": 0, "queries": 0,
    }
    t0 = time.monotonic()
    with drv.session(database=dbn) as session:
        for item in load_items(db):
            if wanted_ids and item["id"] not in wanted_ids:
                continue
            legacy = Retriever(session, corpus).retrieve(item["query"], legacy_cfg, pool_k=pool_k)
            meta: dict = {}
            global_nodes = Retriever(session, corpus).retrieve(
                item["query"], global_cfg, pool_k=pool_k, meta=meta)
            legacy_names = {node["name"] for node in legacy}
            global_names = {node["name"] for node in global_nodes}
            anchor_names = {node["name"] for node in global_nodes if node.get("role") == "anchor"}
            gold = set(item["gold"])
            lh, gh, ah = gold & legacy_names, gold & global_names, gold & anchor_names
            rescued = sorted(gh - ah)
            missing = sorted(gold - global_names)
            totals["queries"] += 1
            totals["gold"] += len(gold)
            totals["legacy"] += len(lh)
            totals["global"] += len(gh)
            totals["anchor"] += len(ah)
            totals["legacy_size"] += len(legacy)
            totals["global_size"] += len(global_nodes)
            suffix = []
            if rescued:
                suffix.append(f"evidence={rescued}")
            if missing:
                ranks = {
                    row["name"]: row["rank"]
                    for row in meta.get("selection", {}).get("ranked_candidates", [])
                    if row["name"] in missing
                }
                suffix.append(f"MISS={missing} candidate_rank={ranks}")
            print(
                f"[{db}] {item['id']:<16} legacy {len(lh)}/{len(gold)} n={len(legacy):<3} | "
                f"anchor {len(ah)}/{len(gold)} full {len(gh)}/{len(gold)} n={len(global_nodes):<3} "
                + " ".join(suffix),
                flush=True,
            )
    drv.close()
    totals["seconds"] = round(time.monotonic() - t0, 1)
    return totals


def show(db: str, result: dict) -> None:
    q = result["queries"] or 1
    print(
        f"[{db} 합계] legacy={result['legacy']}/{result['gold']} | "
        f"anchor={result['anchor']}/{result['gold']} | global+evidence={result['global']}/{result['gold']} | "
        f"평균크기 {result['legacy_size']/q:.1f}→{result['global_size']/q:.1f} | {result['seconds']}s"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--pool", type=int, default=30)
    parser.add_argument("--db", choices=("neo4j", "test", "all"), default="all")
    parser.add_argument("--ids", default="", help="쉼표 구분 질의 id 부분집합")
    parser.add_argument("--evidence", type=int, default=DEFAULT.cluster_evidence_top_n)
    args = parser.parse_args()
    ids = {value.strip() for value in args.ids.split(",") if value.strip()} or None
    dbs = ("neo4j", "test") if args.db == "all" else (args.db,)
    for database in dbs:
        show(database, run_db(database, args.pool, ids, args.evidence))
