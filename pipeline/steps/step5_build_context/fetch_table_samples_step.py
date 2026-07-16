"""fetch_table_samples_step — 전략독립. catalog 에서 테이블 실제 샘플행 batch 조회.

step6(GWT)가 실제 값 기반 시나리오를 만들도록, 분석 전(build_context 직후)에
catalog ``sample-context`` 로 테이블별 샘플행을 한 번에 받아 ``table_sample_data`` 에 둔다.
코드노드별 주입은 step6 가 ``ctx.reads/writes`` 로 골라 쓴다 (여기선 전 테이블 수집만 — 노이즈는 step6 책임).

가드 (즉시 skip — graceful):
- ``ROBO_CATALOG_ENABLED=false`` (토글 off)              → ``catalog_base_url_or_skip`` None.
- ``codebase.datasource`` 미지정                          → 운영=AnalyzeRequest / 테스트=ENV.catalog.test_datasource.
- ``tables`` 없음.

성능: 전 테이블을 청크(100)로 나눠 **병렬 호출** (catalog 내부 Semaphore 로 DB 동시쿼리 상한).
복원력: 청크 실패는 **그 테이블만** 샘플 없이 진행(``log_warning`` 표면화) — 분석 전체는 불정지.
"""
from __future__ import annotations

import asyncio
import time
from typing import Optional

import httpx
from langchain_core.runnables import RunnableConfig

from pipeline.execution.step_config import step_config

from pipeline.catalog_client import catalog_base_url_or_skip, post_sample_context
from system.env_catalog import ENV
from log.logger import log_info, log_warning
from log.processing_progress import step_processing_scope

_STEP = "step5.fetch_table_samples"
_CHUNK = 100       # 테이블명 한 요청 당 최대 — 거대 응답/타임아웃 회피
_SAMPLE_LIMIT = 5  # 테이블당 샘플 행 수 (토큰 통제)


async def fetch_table_samples_step(state: dict, config: Optional[RunnableConfig] = None) -> dict:
    cfg = step_config(config)
    stream = cfg.get("stream")
    codebase = cfg.get("codebase")

    base_url = catalog_base_url_or_skip(_STEP)
    if base_url is None:
        return {}

    datasource = getattr(codebase, "datasource", None)
    if not datasource:
        log_info(_STEP, "skip",
                 reason="datasource 미지정 (운영=AnalyzeRequest / 테스트=ENV.catalog.test_datasource)")
        return {}

    table_fqns = [t.id for t in state.get("tables", []) if getattr(t, "id", None)]
    if not table_fqns:
        log_info(_STEP, "skip", reason="no tables")
        return {}

    started = time.monotonic()
    chunks = [table_fqns[i:i + _CHUNK] for i in range(0, len(table_fqns), _CHUNK)]
    log_info(_STEP, "start", datasource=datasource, tables=len(table_fqns), chunks=len(chunks))

    async with httpx.AsyncClient(timeout=ENV.catalog.timeout_seconds) as http:
        with step_processing_scope(_STEP, stream):
            results = await asyncio.gather(
                *[post_sample_context(http, base_url, datasource, c, sample_limit=_SAMPLE_LIMIT)
                  for c in chunks],
                return_exceptions=True,
            )

    # 매칭된 것만 보관 ({fqn: {columns, sample_rows}}). 청크 실패는 표면화 후 그 테이블만 제외.
    sample_data: dict[str, dict] = {}
    failed = 0
    for chunk_map in results:
        if isinstance(chunk_map, BaseException):
            failed += 1
            continue
        for fqn, ctx in chunk_map.items():
            if ctx:
                sample_data[fqn] = ctx
    if failed:
        log_warning(_STEP, "partial",
                    f"{failed}/{len(chunks)} 청크 실패 — 해당 테이블은 샘플 없이 진행")

    log_info(_STEP, "done",
             tables=len(table_fqns), matched=len(sample_data), failed_chunks=failed,
             elapsed_sec=f"{time.monotonic() - started:.2f}")
    return {"table_sample_data": sample_data}
