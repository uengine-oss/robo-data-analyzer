"""enrich_metadata_from_catalog_step — DBMS only.

DBMS catalog 서비스 → Table/Column 메타.

흐름:
1. ``ENV.catalog.base_url`` (운영 단일 진실) 로 외부 HTTP 호출.
2. 테이블별 메타 (sample rows / 통계 / description) 수신.
3. Table.description 갱신 + state 반영.

catalog 서비스 endpoint 가정: ``GET <base_url>/tables/<db>.<schema>.<name>/metadata``.
응답 형식: ``{description, columns: [{name, description, sample_values, ...}]}``.

분석기 영역 = HTTP 응답 처리. 인증/dataset 식별 등 catalog 자체 정책은 운영 영역.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import replace
from typing import Optional

import httpx
from langchain_core.runnables import RunnableConfig

from shared.workflow.step_utils import step_config

from data_models.graph_nodes import Table
from shared.external.catalog_client import get_catalog_json
from shared.config.env_catalog import ENV
from shared.log.logger import log_info, log_warning
from shared.log.processing_progress import step_processing_scope, track_processing
from shared.neo4j.neo4j_client import Neo4jClient
from shared.neo4j.nodes.table import update_descriptions

_STEP = "step7.enrich_metadata_from_catalog"


async def enrich_metadata_from_catalog_step(state: dict, config: Optional[RunnableConfig] = None) -> dict:
    cfg = step_config(config)
    neo: Neo4jClient | None = cfg.get("neo_client")
    stream = cfg.get("stream")
    if not ENV.catalog.enabled:
        log_info(_STEP, "skip", reason="catalog 비활성화 (ROBO_CATALOG_ENABLED=false)")
        return {}
    if not ENV.catalog.base_url:
        log_info(_STEP, "skip", reason="ROBO_CATALOG_BASE_URL 미설정")
        return {}

    tables = state.get("tables", [])
    if not tables:
        log_info(_STEP, "skip", reason="no tables")
        return {}
    started = time.monotonic()
    log_info(_STEP, "start",
             catalog_url=ENV.catalog.base_url, tables=len(tables),
             concurrency=ENV.pipeline.max_concurrency)

    base_url = ENV.catalog.base_url.rstrip("/")
    semaphore = asyncio.Semaphore(ENV.pipeline.max_concurrency)
    updates: list[dict] = []

    async with httpx.AsyncClient(timeout=ENV.catalog.timeout_seconds) as http:

        async def _fetch_one(table):
            """테이블 1건 메타 fetch. track_processing 이 start/done/failed 자동 emit."""
            async with track_processing("Table", table.fqn, sub_action="catalog_metadata"):
                async with semaphore:
                    url = f"{base_url}/tables/{table.fqn}/metadata"
                    # 404 = 정상 (이 테이블이 catalog 에 없을 뿐). non-200/파싱 실패는 전파.
                    data = await get_catalog_json(http, url, label="metadata")
                    return (table, data) if data is not None else None

        with step_processing_scope(_STEP, stream):
            results = await asyncio.gather(*[_fetch_one(t) for t in tables])

    for pair in results:
        if pair is None:
            continue
        table, data = pair
        desc = data.get("description") or data.get("comment") or ""
        if desc:
            updates.append({
                "fqn": table.fqn,
                "description": desc,
            })

    if neo is not None and updates:
        await update_descriptions(neo, updates)

    # state 반영 — catalog description 박은 새 Table 인스턴스로 교체.
    fqn_to_desc = {u["fqn"]: u["description"] for u in updates}
    updated_tables = [
        replace(t, description=fqn_to_desc[t.fqn], description_source="catalog")
        for t in tables
        if t.fqn in fqn_to_desc
    ]

    missed = sum(1 for r in results if r is None)
    log_info(_STEP, "done",
             tables=len(tables), updated=len(updates), catalog_miss=missed,
             elapsed_sec=f"{time.monotonic() - started:.2f}")
    return {"tables": updated_tables}
