"""메타데이터 보강 - DBMS

catalog 서비스(robo-data-catalog)에 메타데이터 보강을 위임합니다.
Text2SQL 샘플 조회, LLM 설명 생성, FK 추론은 모두 catalog에서 처리.
"""

import logging
import os
from typing import Any, AsyncGenerator

import aiohttp

from config.settings import settings
from analyzer.strategy.base_analyzer import AnalysisStats
from util.stream_emitter import emit_message, emit_phase_event
from util.logger import log_process

logger = logging.getLogger(__name__)

PHASE_NAME = "메타데이터 보강"


async def run_phase_metadata(
    ctx: Any,  # DbmsPhaseContext
    phase_num: int = 3,
) -> AsyncGenerator[bytes, None]:
    """메타데이터 보강 — catalog 서비스에 위임"""
    yield emit_message(f"   🚀 [Phase {phase_num}] {PHASE_NAME} 시작...")
    yield emit_phase_event(
        phase_num=phase_num,
        phase_name=PHASE_NAME,
        status="in_progress",
        progress=0,
        details={"step": "init"},
    )

    datasource_name = ctx.datasource_name
    api_key = ctx.api_key

    if not datasource_name:
        yield emit_message("   ⏭️ datasource 미설정, 메타데이터 보강 생략")
        yield emit_phase_event(phase_num=phase_num, phase_name=PHASE_NAME, status="completed", progress=100)
        return

    catalog_url = os.environ.get("CATALOG_SERVICE_URL", "http://localhost:5503")
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
        log_process("PHASE:3", "ERROR", f"catalog 보강 실패: {e}", logging.WARNING)
        yield emit_message(f"   ⚠️ 메타데이터 보강 실패 (무시): {str(e)[:80]}")

    yield emit_phase_event(phase_num=phase_num, phase_name=PHASE_NAME, status="completed", progress=100)
