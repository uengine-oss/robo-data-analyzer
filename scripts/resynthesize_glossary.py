"""resynthesize_glossary — 그래프의 domain_terms_json으로 용어사전 종합만 재실행.

전량 재분석(수 분·요약 재생성 변동) 없이, 종합 프롬프트/병합 로직 개선을 반복 검증한다.
수집·문맥조립·수용·그룹 로직은 build_glossary_step 의 것을 그대로 재사용(단일 진실).

사용::

    # .env의 ROBO_NEO4J_DATABASE 대상(Analyzer SETTINGS로 해석)
    python scripts/resynthesize_glossary.py --strategy framework
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline.steps.step7_post_processing.build_glossary_step import (
    _accept, _build_contexts, _collect_terms, _union_groups, _UPSERT_QUERY,
)
from pipeline.strategies import PIPELINES
from pipeline.models.code_analysis_output import GlossarySynthesisOutput
from analyzer_settings import SETTINGS
from llm.llm_fanout import gather_with_warmup
from llm.runners.yaml_llm_runner import run_yaml_rule
from graph.schema.domain_terms import load_domain_terms
from graph.ownership import ANALYZER_GRAPH_OWNER

_NODES_QUERY = f"""
MATCH (n {{graph_owner: '{ANALYZER_GRAPH_OWNER}'}})
WHERE n.domain_terms_json IS NOT NULL AND n.domain_terms_json <> ''
RETURN n.id AS id, n.name AS name, coalesce(n.file_path,'') AS file_path,
       coalesce(n.start_line,0) AS start_line,
       coalesce(n.summary,'') AS summary, coalesce(n.search_text,'') AS search_text,
       n.domain_terms_json AS domain_terms_json
"""
_MODULES_QUERY = f"""
MATCH (m {{graph_owner: '{ANALYZER_GRAPH_OWNER}'}})
WHERE (m:MODULE OR m:FILE) AND m.summary IS NOT NULL
RETURN m.id AS id, coalesce(m.file_path,'') AS file_path,
       coalesce(m.start_line,0) AS start_line, coalesce(m.end_line,0) AS end_line,
       m.summary AS summary
"""


def _fetch(db: str) -> tuple[list, list]:
    from neo4j import GraphDatabase
    drv = GraphDatabase.driver(SETTINGS.graph_database.uri, auth=(SETTINGS.graph_database.user, SETTINGS.graph_database.password))
    with drv.session(database=db) as s:
        nodes = []
        for record in s.run(_NODES_QUERY).data():
            record["domain_terms"] = load_domain_terms(record.pop("domain_terms_json"))
            nodes.append(SimpleNamespace(**record))
        modules = [SimpleNamespace(**r) for r in s.run(_MODULES_QUERY).data()]
    drv.close()
    return nodes, modules


async def _run(strategy_name: str) -> None:
    db = SETTINGS.graph_database.database
    nodes, modules = _fetch(db)
    terms = _collect_terms(nodes)
    if not terms:
        print(f"[{db}] domain_terms 조각 0 — 재종합 불가(재분석 필요)")
        return
    contexts = _build_contexts(terms, nodes, modules)
    catalog = {k: v["canonical"] for k, v in terms.items()}
    term_catalog = "\n".join(f"- {v['canonical']}" for v in terms.values())
    prompts_dir = Path(PIPELINES[strategy_name].prompts_dir)
    semaphore = asyncio.Semaphore(SETTINGS.analysis_pipeline.max_concurrency)
    started = time.monotonic()

    async def one(key: str):
        slot = terms[key]
        async with semaphore:
            return key, await run_yaml_rule(
                prompts_dir=prompts_dir, rule_name="glossary_synthesis",
                user_values={"term_catalog": term_catalog, "term": slot["canonical"],
                             "meanings": " / ".join(slot["meanings"]), "context": contexts[key]},
                response_model=GlossarySynthesisOutput, budget=None,
            )

    keys = list(terms)
    results = await gather_with_warmup([one(k) for k in keys], return_exceptions=True)
    accepted, failed, rejected = {}, 0, 0
    for key, result in zip(keys, results):
        if isinstance(result, BaseException):
            failed += 1
            continue
        res = _accept(result[1], contexts[key], catalog, key)
        if res is None:
            rejected += 1
            continue
        accepted[key] = res

    final_terms = _union_groups(terms, accepted)
    payload = json.dumps({"terms": final_terms, "actions": []}, ensure_ascii=False)
    from neo4j import GraphDatabase
    drv = GraphDatabase.driver(SETTINGS.graph_database.uri, auth=(SETTINGS.graph_database.user, SETTINGS.graph_database.password))
    with drv.session(database=db) as s:
        s.run(_UPSERT_QUERY, items=[{"terms_json": payload, "n_terms": len(final_terms)}])
    drv.close()
    print(f"[{db}] 수집 {len(terms)} → 종합 {len(accepted)} (기각 {rejected}·실패 {failed}) "
          f"→ 그룹 {len(final_terms)}  ({time.monotonic() - started:.1f}s)")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--strategy", default="framework", choices=["framework", "dbms"])
    args = ap.parse_args()
    asyncio.run(_run(args.strategy))
