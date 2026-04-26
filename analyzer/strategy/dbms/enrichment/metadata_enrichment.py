"""DBMS 메타데이터 보강 — robo-data-catalog 서비스에 위임.

원본 위치 (이전):
  - `analyzer/strategy/dbms/pipeline/phase_metadata.py::run_phase_metadata`

책임:
  - catalog `/robo/schema/enrich-metadata` POST — Text2SQL 샘플 + LLM 설명 + FK 추론
  - phase_metadata.py 는 본 모듈을 호출만.

정책 정렬:
  - datasource 미설정 시 명시적 skip + 로깅 (DDL only 시나리오에서는 정상 분기).
  - catalog 호출 실패는 errlog + raise (silent skip 금지).
"""

from __future__ import annotations

import logging
from typing import AsyncGenerator

import aiohttp

from util.logger import log_process
from util.stream_emitter import emit_message, emit_phase_event

logger = logging.getLogger(__name__)


PHASE_NAME = "메타데이터 보강"


async def run_metadata_enrichment(
    *,
    datasource_name: str,
    api_key: str,
    catalog_url: str,
    phase_num: int = 3,
) -> AsyncGenerator[bytes, None]:
    """catalog `/robo/schema/enrich-metadata` POST 로 메타데이터 보강 위임.

    호출 실패는 errlog + raise. 정책상 silent skip 금지.

    emit_phase_event 는 `in_progress` 만 yield. `started`/`completed` 는 호출자(dbms_pipeline) 가
    감싸 emit (Framework 패턴 통일).
    """
    yield emit_message(f"   🚀 [Phase {phase_num}] {PHASE_NAME} 시작...")
    yield emit_phase_event(
        phase_num=phase_num,
        phase_name=PHASE_NAME,
        status="in_progress",
        progress=0,
        details={"step": "init"},
    )

    if not datasource_name:
        yield emit_message("   ⏭️ datasource 미설정, 메타데이터 보강 생략")
        return

    try:
        async with aiohttp.ClientSession() as session:
            resp = await session.post(
                f"{catalog_url}/robo/schema/enrich-metadata",
                json={"datasource_name": datasource_name},
                headers={"OpenAI-Api-Key": api_key},
                timeout=aiohttp.ClientTimeout(total=300),
            )
            result = await resp.json()
            enriched = result.get("enriched", 0)
            msg = result.get("message", "")
            yield emit_message(f"   ✅ 메타데이터 보강 완료: {msg}")
            log_process("PHASE:3", "COMPLETE", f"enriched={enriched}", logging.INFO)
    except Exception as e:
        logger.error(
            "[PHASE:3:METADATA] catalog 보강 실패 | datasource=%s | %s: %s",
            datasource_name, type(e).__name__, e, exc_info=True,
        )
        raise RuntimeError(
            f"catalog 메타데이터 보강 실패 (datasource={datasource_name}): {type(e).__name__}: {e}"
        ) from e


__all__ = ["run_metadata_enrichment"]
