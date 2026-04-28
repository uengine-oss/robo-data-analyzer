"""DBMS 메타데이터 보강 — robo-data-catalog 서비스에 위임.

원본 위치 (이전):
  - `analyzer/strategy/dbms/pipeline/phase_metadata.py::run_phase_metadata`

책임:
  - catalog `/robo/schema/enrich-metadata` POST — Text2SQL 샘플 + LLM 설명 + FK 추론
  - catalog 가 NDJSON 스트림을 반환 → 본 모듈이 한 줄씩 파싱하여 user-facing message 로 relay
  - phase_metadata.py 는 본 모듈을 호출만.

정책 정렬 (Framework 의 pre_analysis.py 와 동일 패턴):
  - catalog 비활성 (`CATALOG_SERVICE_URL` 미설정) → INFO + skip (정상 시나리오)
  - catalog 활성인데 호출 실패 → fail-fast (사용자가 활성화했는데 안 떠있는 건 진짜 에러)
  - datasource 미설정 → 명시적 skip (DDL only 시나리오)
"""

from __future__ import annotations

import json
import logging
from typing import Any, AsyncGenerator, Dict

import aiohttp

from config.settings import settings
from util.logger import log_process
from util.stream_emitter import emit_message, emit_phase_event

logger = logging.getLogger(__name__)


PHASE_NAME = "메타데이터 보강"


def _format_event(evt: Dict[str, Any]) -> str:
    """catalog NDJSON 이벤트 → user-facing 한국어 메시지."""
    e = evt.get("event")
    if e == "skip":
        return f"   ⏭️ {evt.get('reason', '생략')}"
    if e == "start":
        phase = evt.get("phase", "")
        total = evt.get("total")
        if phase == "description":
            return f"   📋 description 생성 시작 ({total}개 테이블)"
        return f"   ▶️ {phase} 시작"
    if e == "table_done":
        i, total = evt.get("i"), evt.get("total")
        table = evt.get("table", "")
        ok = "✓" if evt.get("description_persisted") else "·"
        return f"      {ok} {i}/{total} {table}"
    if e == "phase_done":
        phase = evt.get("phase", "")
        if phase == "description":
            return f"   ✅ description 보강 완료: {evt.get('enriched', 0)}개 테이블"
        return f"   ✅ {phase} 완료"
    if e == "fk_query_start":
        return (
            f"   🔍 FK 추론 시작 (schema={evt.get('schema')}, "
            f"overlap≥{evt.get('overlap_threshold', evt.get('threshold'))}, "
            f"confidence≥{evt.get('confidence_threshold', '?')})"
        )
    if e == "fk_query_done":
        return f"   🔗 1차 후보(값 overlap): {evt.get('candidate_count', 0)}개"
    if e == "fk_filter_applied":
        before = evt.get("before", 0)
        after = evt.get("after", 0)
        thr = evt.get("confidence_threshold", "?")
        rej = evt.get("rejected_examples") or []
        rej_str = ""
        if rej:
            rej_str = " | 제외 예시: " + ", ".join(
                f"{r['src']}↔{r['tgt']}(conf={r['confidence']:.2f})"
                for r in rej[:3]
            )
        return f"   🧹 신뢰도 필터(conf≥{thr}): {before} → {after}개{rej_str}"
    if e == "fk_persisted":
        return (
            f"   💾 FK 영속화: column-level {evt.get('fk_to_column', 0)}건, "
            f"table-level {evt.get('fk_to_table', 0)}건"
        )
    if e == "fk_error":
        return f"   ⚠️ FK 추론 실패: {evt.get('message', '')}"
    if e == "complete":
        return (
            f"   🎉 메타데이터 보강 완료 — "
            f"description {evt.get('description_enriched', 0)}, "
            f"FK {evt.get('fk_persisted', 0)}"
        )
    if e == "error":
        return f"   ❌ 오류: {evt.get('message', '')}"
    # unknown event → raw JSON
    return f"   · {json.dumps(evt, ensure_ascii=False)}"


async def run_metadata_enrichment(
    *,
    datasource_name: str,
    api_key: str,
    catalog_url: str,
    phase_num: int = 3,
) -> AsyncGenerator[bytes, None]:
    """catalog `/schema/enrich-metadata` 스트림을 받아 user-facing 으로 relay.

    catalog 응답: NDJSON. 한 줄당 한 이벤트.
    호출 실패는 errlog + raise. 정책상 silent skip 금지.
    """
    yield emit_message(f"   🚀 [Phase {phase_num}] {PHASE_NAME} 시작...")
    yield emit_phase_event(
        phase_num=phase_num,
        phase_name=PHASE_NAME,
        status="in_progress",
        progress=0,
        details={"step": "init"},
    )

    # Framework 의 pre_analysis 와 동일 패턴 — catalog 비활성 시 graceful skip
    if not settings.catalog.enabled:
        yield emit_message("   ⏭️ catalog 비활성 (CATALOG_SERVICE_URL 미설정) — 메타데이터 보강 skip")
        log_process("PHASE:3", "SKIP", "catalog 비활성 — 메타데이터 보강 skip", logging.INFO)
        return

    if not datasource_name:
        yield emit_message("   ⏭️ datasource 미설정, 메타데이터 보강 생략")
        return

    description_count = 0
    fk_count = 0

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{catalog_url}/robo/schema/enrich-metadata",
                json={"datasource_name": datasource_name},
                headers={"OpenAI-Api-Key": api_key},
                timeout=aiohttp.ClientTimeout(total=1800),  # 30분 (LLM 다수 호출 대비)
            ) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    raise RuntimeError(
                        f"catalog HTTP {resp.status}: {body[:200]}"
                    )

                # NDJSON 라인별 파싱 → relay
                async for raw_line in resp.content:
                    line = raw_line.strip()
                    if not line:
                        continue
                    try:
                        evt = json.loads(line.decode("utf-8"))
                    except (UnicodeDecodeError, json.JSONDecodeError):
                        continue

                    if evt.get("event") == "complete":
                        description_count = int(evt.get("description_enriched", 0))
                        fk_count = int(evt.get("fk_persisted", 0))

                    yield emit_message(_format_event(evt))

        log_process(
            "PHASE:3",
            "COMPLETE",
            f"description={description_count} fk={fk_count}",
            logging.INFO,
        )
    except Exception as e:
        logger.error(
            "[PHASE:3:METADATA] catalog 보강 실패 | datasource=%s | %s: %s",
            datasource_name, type(e).__name__, e, exc_info=True,
        )
        raise RuntimeError(
            f"catalog 메타데이터 보강 실패 (datasource={datasource_name}): {type(e).__name__}: {e}"
        ) from e


__all__ = ["run_metadata_enrichment"]
