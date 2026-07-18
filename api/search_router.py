"""검색 라우터 — 요구사항 → 관련 코드 뭉치 (spec 044, NDJSON 스트림).

``POST /robo/search/semantic`` — 자연어 요구사항을 받아 관련 코드 뭉치를 **NDJSON 스트림**으로:
``progress`` × N → ``result`` → ``stream_end``. 실패는 ``error``, 결과 0건은 빈 ``clusters``.

배선(analyze 규약 재사용): ``set_override`` 로 요청 헤더의 DB 를 이 요청 컨텍스트에 싣고,
``create_task`` 가 컨텍스트를 복사한다. 검색 코어는 sync 라 ``to_thread`` 로 격리하고, progress 는
워커 스레드→이벤트루프로 ``call_soon_threadsafe`` 로 안전하게 흘린다(asyncio.Queue 는 스레드 비안전).
검색은 읽기전용이라 동시 여러 건 허용(analyze 의 "동시 1건" registry 미적용).
"""
from __future__ import annotations

import asyncio
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from search.corpus.corpus import SearchUnavailable
from search.service import run_search
from runtime_events.runtime_logger import log_error, log_info
from runtime_events.ndjson_event_stream import NDJSONStream
from graph.connection_context import Neo4jOverride, set_override

router = APIRouter(prefix="/robo")


class SearchRequest(BaseModel):
    query: str
    top_n: Optional[int] = Field(default=None, ge=1, le=100)


@router.post("/search/semantic")
async def search_semantic(req: SearchRequest, request: Request) -> StreamingResponse:
    """요구사항 → 관련 코드 뭉치 (NDJSON 스트림). 요청 헤더 DB 대상, 동시 허용."""
    query = (req.query or "").strip()
    if not query:
        raise HTTPException(status_code=400, detail="빈 질의")

    try:
        set_override(Neo4jOverride.from_headers(request.headers))
    except ValueError as error:
        raise HTTPException(status_code=400, detail="Invalid Neo4j override headers") from error
    stream = NDJSONStream()
    loop = asyncio.get_running_loop()

    async def _run() -> None:
        try:
            def on_progress(stage: str, label: str, detail: str) -> None:
                loop.call_soon_threadsafe(stream.emit, {
                    "type": "progress", "stage": stage, "label": label,
                    "detail": detail, "done": False,
                })
            clusters, meta = await asyncio.to_thread(
                run_search, query, on_progress, top_n=req.top_n,
            )
            log_info("api.search", "done", query=query,
                     nodes=sum(len(c["nodes"]) for c in clusters))
            stream.emit({"type": "result", "clusters": clusters, "meta": meta})
        except SearchUnavailable as e:
            stream.emit({"type": "error", "message": str(e)})
        except Exception as e:  # noqa: BLE001 — 표면화(헌법 IV): 조용한 실패 금지
            log_error("api.search", "search_failed", e, query=query)
            stream.emit({
                "type": "error",
                "message": "검색 처리에 실패했습니다.",
                "error_type": type(e).__name__,
            })
        finally:
            await stream.close()

    producer = asyncio.create_task(_run())
    return StreamingResponse(
        stream.iter_with_producer(producer),
        media_type="application/x-ndjson",
    )
