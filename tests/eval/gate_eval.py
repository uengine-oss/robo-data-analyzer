"""tests/eval/gate_eval — 검색 게이트 상시 측정 (놀이터 이관·정착, spec 044 SC-001).

analyzer `search/` 코어를 골드(cluster_gold + rewrite_gold)로 채점 → gold 이름 회수율.
현 config(rewrite/judge/ce_top)를 그대로 쓰되 env 로 A/B 가능:
  ROBO_SEARCH_JUDGE=off|p15  ROBO_SEARCH_REWRITE=off|novel_abbrev  ROBO_SEARCH_CE_TOP=15|30

실행: ./.venv/Scripts/python.exe tests/eval/gate_eval.py [--pool 30]
CE 점수는 `.search_cache/ce_scores.json` 디스크 캐시 — 첫 실행만 느리고 재측정은 즉시.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT))
import yaml

from search.search_config import DEFAULT
from search.corpus.corpus import load_corpus
from search.corpus.neo4j_reader import read_driver
from search.ranking.retriever import Retriever
from system.env_catalog import ENV
from graph.connection_context import Neo4jOverride, set_override

_GOLD = Path(__file__).resolve().parent


def load_items(db: str) -> list[dict]:
    items = list(yaml.safe_load((_GOLD / "cluster_gold.yaml").read_text(encoding="utf-8"))["datasets"].get(db, []))
    rg = yaml.safe_load((_GOLD / "rewrite_gold.yaml").read_text(encoding="utf-8"))["datasets"].get(db, {})
    for it in rg.get("gap", []):
        items.append({"id": it["id"], "query": it["query"], "gold": it.get("gold", [])})
    for it in rg.get("regression", []):
        items.append({"id": it["id"], "query": it["query"], "gold": it.get("keep", [])})
    return items


def run_db(db: str, pool_k: int) -> tuple[int, int, list]:
    set_override(Neo4jOverride(ENV.neo4j.uri, ENV.neo4j.user, ENV.neo4j.password, db))
    corpus = load_corpus()
    drv, dbn = read_driver()
    hits = total = 0
    misses = []
    t0 = time.monotonic()
    with drv.session(database=dbn) as s:
        for it in load_items(db):
            nodes = Retriever(s, corpus).retrieve(it["query"], DEFAULT, pool_k=pool_k)
            names = {n["name"] for n in nodes}
            g = [x for x in it["gold"] if x in names]
            hits += len(g); total += len(it["gold"])
            miss = sorted(set(it["gold"]) - set(g))
            print(f"  [{db}] {it['id']:<16} {len(g)}/{len(it['gold'])}  n={len(nodes):<3} "
                  f"{'OK' if not miss else 'MISS ' + str(miss)}", flush=True)
            if miss:
                misses.append((it["id"], miss))
    drv.close()
    print(f"  [{db}] 합계 {hits}/{total}  ({time.monotonic() - t0:.0f}s)", flush=True)
    return hits, total, misses


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--pool", type=int, default=30)
    args = ap.parse_args()
    print(f"════ 게이트 pool_k={args.pool} · rewrite={DEFAULT.cluster_rewrite_mode} "
          f"judge={DEFAULT.cluster_judge_mode} ce_top={DEFAULT.cluster_ce_top} · LLM={ENV.search.llm_config} ════", flush=True)
    nh, nt, nm = run_db("neo4j", args.pool)
    th, tt, tm = run_db("test", args.pool)
    print(f"\n════ 합산 {nh + th}/{nt + tt}  (neo4j {nh}/{nt} · test {th}/{tt}) ════")
    if nm or tm:
        print(f"미회수: {nm + tm}")
