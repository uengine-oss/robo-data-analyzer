"""Table description LLM 보강 오케스트레이션 — strategy-agnostic.

호출자(phase) 책임:
  - 자기 데이터 (in-memory store / Neo4j 결과) 를 canonical TableInput 으로 변환
  - LLM 호출 + Cypher 빌더 콜백 주입 (strategy 마다 노드 식별자 / 프롬프트 다름)

이 모듈 책임:
  - 병렬 LLM 호출 + 진행률 emit + Neo4j 쓰기 루프 (orchestration)

canonical 입력 (TableInput):
  {
    "extra": Any,   # phase 가 Cypher 빌더에 넘길 임의 컨텍스트 (schema, datasource 등)
    "payload": Dict # invoke_llm 콜백에 그대로 전달
  }
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, AsyncGenerator, Awaitable, Callable, Dict, List, Optional

from analyzer.common.cypher.graph_writer import write_to_graph
from client.neo4j_client import Neo4jClient
from util.stream_emitter import emit_user_message, emit_phase_event

logger = logging.getLogger(__name__)


async def run_table_enrichment(
    *,
    client: Neo4jClient,
    table_inputs: List[Dict[str, Any]],
    invoke_llm: Callable[[Dict[str, Any]], Awaitable[Optional[Dict[str, Any]]]],
    build_queries: Callable[[Any, Dict[str, Any]], List[Dict[str, Any]]],
    log_tag: str,
    phase_num: int,
    progress_start: int,
    progress_end: int,
    step_label: str = "테이블/컬럼 설명 요약",
) -> AsyncGenerator[bytes, None]:
    """병렬 LLM 호출 + Neo4j 쓰기 + 진행률 emit.

    Args:
        table_inputs: [{"extra", "payload"}, ...]
        invoke_llm:   payload → LLM 결과 dict (실패 시 None)
        build_queries: (extra, llm_result) → Cypher 쿼리 dict 리스트
    """
    if not table_inputs:
        yield emit_phase_event(
            phase_num, "AI 분석", "in_progress", progress_end,
            {"step": f"{step_label} 완료", "tables": 0},
        )
        return

    total = len(table_inputs)
    yield emit_phase_event(
        phase_num, "AI 분석", "in_progress", progress_start,
        {"step": step_label, "total": total},
    )

    async def _wrap(entry: Dict[str, Any]):
        result = await invoke_llm(entry["payload"])
        return entry, result

    tasks = [asyncio.create_task(_wrap(e)) for e in table_inputs]

    completed = 0
    total_q = 0
    for fut in asyncio.as_completed(tasks):
        completed += 1
        entry, result = await fut
        queries = build_queries(entry.get("extra"), result) if result else []
        if queries:
            total_q += len(queries)
            yield emit_user_message(f"   ✓ [{completed}/{total}] 테이블 요약 완료")
            async for chunk in write_to_graph(client, queries, log_tag):
                yield chunk
        progress = progress_start + int(completed / total * (progress_end - progress_start))
        yield emit_phase_event(
            phase_num, "AI 분석", "in_progress", progress,
            {"step": step_label, "done": completed, "total": total},
        )

    yield emit_phase_event(
        phase_num, "AI 분석", "in_progress", progress_end,
        {"step": f"{step_label} 완료", "tables": total, "queries": total_q},
    )
    logger.info(
        "[%s] table enrichment 완료 | tables=%d queries=%d", log_tag, total, total_q,
    )


__all__ = ["run_table_enrichment"]
