"""reembed_graph — 재분석 없이 저장된 그래프에 검색 임베딩·마커·인덱스만 재구성 (spec 044 FR-011).

전량 재분석(수 분·요약 재생성 변동) 없이 embed_step 의 임베딩 로직만 재실행한다. 코드노드
텍스트 조성·저장·:EMBEDDED·벡터 인덱스·지문은 embed_step 의 것을 **그대로 재사용**(단일 진실) —
코드노드 재료는 embed_step 이 그래프(FETCH_MATERIAL)에서 직접 읽으므로 state 는 비워 넘긴다.
검색 대상이 아닌 Table/Column 임베딩은 건드리지 않는다(catalog/text2sql 자체 관리 — plan §B).

사용::

    # .env 의 ROBO_NEO4J_DATABASE 대상
    python cli_scripts/reembed_graph.py
    # 특정 DB 지정 (uri/user/password 는 .env)
    python cli_scripts/reembed_graph.py --database neo4j
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
from pathlib import Path

# reembed 는 명시적 임베딩 요청 — 파이프라인 자동임베딩 토글(ROBO_PIPELINE_EMBED_ENABLED,
# 기본 off)과 무관하게 항상 실행한다. ENV 는 import 시 frozen 되므로 analyzer import 전에 강제.
# (load_dotenv 는 override=False 라 .env 의 false 가 이 값을 덮지 않는다.)
os.environ["ROBO_PIPELINE_EMBED_ENABLED"] = "true"

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from analysis_flow.step7_post_processing.embed_step import embed_step
from search.embed_material import EMBED_META_LABEL, EMBEDDED_LABEL, VECTOR_INDEX_NAME
from system.env_catalog import ENV
from graph.connection_context import Neo4jOverride, set_override
from graph.neo4j_client import Neo4jClient


async def _verify(neo: Neo4jClient) -> None:
    """재구성 결과 실측 — EMBEDDED 수·차원 분포·인덱스 상태·지문 (손실/차원 점검)."""
    total = (await neo.run_read_query(
        "MATCH (n) WHERE n.summary IS NOT NULL AND n.summary <> '' "
        "AND NOT n:FILE AND NOT n:MODULE AND NOT n:PACKAGE RETURN count(n) AS c"))[0]["c"]
    embedded = (await neo.run_read_query(
        f"MATCH (n:{EMBEDDED_LABEL}) RETURN count(n) AS c"))[0]["c"]
    dims = await neo.run_read_query(
        f"MATCH (n:{EMBEDDED_LABEL}) WITH size(n.embedding) AS d "
        "RETURN d AS dim, count(*) AS c ORDER BY dim")
    meta = await neo.run_read_query(
        f"MATCH (m:{EMBED_META_LABEL}) RETURN m.fingerprint AS fp, m.dim AS dim")
    idx = await neo.run_read_query(
        "SHOW INDEXES YIELD name, state, type WHERE name = $n", {"n": VECTOR_INDEX_NAME})

    print("\n── 재구성 검증 ──────────────────────────────")
    print(f"대상 코드노드(summary 보유) : {total}")
    print(f":EMBEDDED 부여            : {embedded}   (손실 {total - embedded})")
    print(f"차원 분포                 : {[(d['dim'], d['c']) for d in dims]}")
    print(f":EMBED_META               : {meta}")
    print(f"벡터 인덱스 '{VECTOR_INDEX_NAME}' : {idx}")
    ok = embedded == total and len(dims) == 1 and idx and idx[0].get("state") == "ONLINE"
    print(f"판정                      : {'OK (손실 0·단일 차원·인덱스 ONLINE)' if ok else 'CHECK 필요'}")


async def _run(database: str | None) -> None:
    if database:
        set_override(Neo4jOverride(
            uri=ENV.neo4j.uri, user=ENV.neo4j.user,
            password=ENV.neo4j.password, database=database))
    neo = Neo4jClient()
    db = database or ENV.neo4j.database
    started = time.monotonic()
    print(f"[{db}] 재임베딩 시작 (backend={ENV.embed.provider}:{ENV.embed.model})")
    try:
        # code_nodes 는 embed_step 이 그래프에서 직접 읽음. tables/columns 는 검색 무관 → 스킵.
        await embed_step({"tables": [], "columns": [], "code_nodes": []},
                         {"configurable": {"neo_client": neo, "stream": None}})
        print(f"[{db}] embed_step 완료 ({time.monotonic() - started:.1f}s)")
        await _verify(neo)
    finally:
        await neo.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--database", default=None, help="대상 DB (기본 .env ROBO_NEO4J_DATABASE)")
    args = ap.parse_args()
    asyncio.run(_run(args.database))
