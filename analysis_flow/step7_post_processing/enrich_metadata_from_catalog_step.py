"""enrich_metadata_from_catalog_step — DBMS only.

DBMS catalog 샘플 데이터 → Table/Column 메타.

흐름:
1. ``project.catalog_service_url`` + ``catalog_api_key`` 로 외부 HTTP 호출.
2. 테이블별 메타 (sample rows / 통계 / description) 수신.
3. Table.description 갱신.

catalog 서비스 endpoint 가정: ``GET /tables/<db>.<schema>.<name>/metadata``.
응답 형식: ``{description, columns: [{name, description, sample_values, ...}]}``.
"""
from __future__ import annotations

import asyncio
import time
from typing import Optional

import httpx
from langchain_core.runnables import RunnableConfig

from shared.workflow_runner.step_config import step_config

from shared.catalog_http import catalog_headers, get_catalog_json
from shared.config.env_catalog import ENV
from shared.logging_and_stream.logger import log_info, log_warning
from shared.neo4j.neo4j_client import Neo4jClient
from shared.neo4j.nodes.table import update_descriptions

_STEP = "step7.enrich_metadata_from_catalog"


async def enrich_metadata_from_catalog_step(state: dict, config: Optional[RunnableConfig] = None) -> dict:
    cfg = step_config(config)
    project = cfg.get("project")
    neo: Neo4jClient | None = cfg.get("neo_client")
    if project is None:
        log_warning(_STEP, "config_missing", "project 누락 — step skip")
        return {}
    if not ENV.catalog.enabled:
        log_info(_STEP, "skip", reason="catalog 비활성화 (ROBO_CATALOG_ENABLED=false)")
        return {}
    if not getattr(project, "catalog_service_url", None):
        log_info(_STEP, "skip", reason="catalog_service_url 미설정 (이 프로젝트는 catalog 없음)")
        return {}

    tables = state.get("tables", [])
    if not tables:
        log_info(_STEP, "skip", reason="no tables")
        return {}
    started = time.monotonic()
    log_info(_STEP, "start",
             catalog_url=project.catalog_service_url, tables=len(tables),
             concurrency=ENV.pipeline.max_concurrency)

    base_url = project.catalog_service_url.rstrip("/")
    headers = catalog_headers(project.catalog_api_key)

    semaphore = asyncio.Semaphore(ENV.pipeline.max_concurrency)
    updates: list[dict] = []

    async with httpx.AsyncClient(timeout=60.0) as http:

        async def _fetch_one(table):
            async with semaphore:
                url = f"{base_url}/tables/{table.fqn}/metadata"
                # 404 = 정상 (이 테이블이 catalog 에 없을 뿐). non-200/파싱 실패는 전파.
                data = await get_catalog_json(http, url, headers=headers, label="metadata")
                return (table, data) if data is not None else None

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

    missed = sum(1 for r in results if r is None)
    log_info(_STEP, "done",
             tables=len(tables), updated=len(updates), catalog_miss=missed,
             elapsed_sec=f"{time.monotonic() - started:.2f}")
    return {}
