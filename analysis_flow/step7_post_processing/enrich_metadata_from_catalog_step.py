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

from shared.config.env_catalog import ENV
from shared.logging_and_stream.logger import log_info, log_warning
from shared.neo4j.neo4j_client import Neo4jClient
from shared.neo4j.nodes.table import update_descriptions

_STEP = "step7.enrich_metadata_from_catalog"


async def enrich_metadata_from_catalog_step(state: dict, config: Optional[RunnableConfig] = None) -> dict:
    cfg = (config or {}).get("configurable", {}) if isinstance(config, dict) else {}
    project = cfg.get("project")
    neo: Neo4jClient | None = cfg.get("neo_client")
    if project is None:
        log_warning(_STEP, "config_missing", "project 누락 — step skip")
        return {}
    if not getattr(project, "catalog_service_url", None):
        log_info(_STEP, "skip", reason="catalog_service_url 미설정 (Framework / catalog 미연동 정상)")
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
    headers = {}
    if project.catalog_api_key:
        headers["Authorization"] = f"Bearer {project.catalog_api_key}"

    semaphore = asyncio.Semaphore(ENV.pipeline.max_concurrency)
    updates: list[dict] = []

    async with httpx.AsyncClient(timeout=60.0) as http:

        async def _fetch_one(table):
            async with semaphore:
                fqn = f"{table.db}.{table.schema}.{table.name}"
                url = f"{base_url}/tables/{fqn}/metadata"
                try:
                    resp = await http.get(url, headers=headers)
                except httpx.HTTPError as e:
                    log_warning(
                        "analysis_flow.step7_post_processing.enrich_metadata_from_catalog_step",
                        "catalog_fetch",
                        "catalog HTTP 오류 — 이 테이블 metadata enrichment 건너뜀",
                        table_fqn=fqn,
                        error=f"{type(e).__name__}: {e}",
                    )
                    return None
                # 404 = 정상 (catalog 에 없음 — feedback memory: catalog_miss_skip_ok)
                if resp.status_code == 404:
                    return None
                if resp.status_code != 200:
                    log_warning(
                        "analysis_flow.step7_post_processing.enrich_metadata_from_catalog_step",
                        "catalog_fetch",
                        "catalog non-200 응답 — 이 테이블 건너뜀",
                        table_fqn=fqn,
                        status=resp.status_code,
                    )
                    return None
                try:
                    return table, resp.json()
                except ValueError as e:
                    log_warning(
                        "analysis_flow.step7_post_processing.enrich_metadata_from_catalog_step",
                        "catalog_fetch",
                        "catalog 응답 JSON 파싱 실패 — 이 테이블 건너뜀",
                        table_fqn=fqn,
                        error=str(e),
                    )
                    return None

        results = await asyncio.gather(*[_fetch_one(t) for t in tables])

    for pair in results:
        if pair is None:
            continue
        table, data = pair
        desc = data.get("description") or data.get("comment") or ""
        if desc:
            updates.append({
                "fqn": f"{table.db}.{table.schema}.{table.name}",
                "description": desc,
            })

    if neo is not None and updates:
        await update_descriptions(neo, updates)

    missed = sum(1 for r in results if r is None)
    log_info(_STEP, "done",
             tables=len(tables), updated=len(updates), catalog_miss=missed,
             elapsed_sec=f"{time.monotonic() - started:.2f}")
    return {}
