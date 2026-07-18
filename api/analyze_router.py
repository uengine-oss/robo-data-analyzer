"""FastAPI 분석 라우터 — 분석 시작 + 진행 제어.

API key: ``.env`` 의 ``ROBO_LLM_API_KEY`` 단일 진실. 헤더 override 미지원
(``SETTINGS.analysis_llm.api_key`` 는 import 시점에 frozen 되어 런타임 변경 불가 — 글로벌 write 위험 회피).

엔드포인트 2개::

    POST /robo/analyze          — 분석 시작 (NDJSON 스트림 반환)
    POST /robo/analyze/control  — 진행 제어 {action: pause|resume|stop}

진행 상태는 NDJSON 스트림 단일 채널(폴링 없음). 제어는 "동시 분석 1개" 모델 —
``run_registry`` 의 활성 run 컨트롤러에 신호를 전달한다(payload 에 run_id 없음).
"""
from __future__ import annotations

import asyncio
from typing import Literal, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from pipeline.strategies import get_pipeline
from pipeline.models.analysis_meta import Codebase
from analyzer_settings import SETTINGS
from runtime_events.ndjson_event_stream import NDJSONStream
from runtime_events.runtime_logger import log_warning
from graph.connection_context import Neo4jOverride, set_override
from pipeline.execution.run_executor import execute_run
from pipeline.execution.run_registry import get_active


router = APIRouter(prefix="/robo")


class AnalyzeRequest(BaseModel):
    """``/analyze`` 요청 모델.

    catalog **접속 설정**(base_url/enabled)은 운영 영역 (``ROBO_CATALOG_*`` 환경변수).
    단 ``datasource`` ("이번 분석에서 어느 DB 를 샘플링")는 분석 입력값.

    입구 통일(antlr spec 006 · analyzer spec 014): 분석기는 입력 모드와 무관하게 항상
    표준 ``SETTINGS.analysis_data.dir/{source, ddl, analysis}`` 만 소비한다. 소스의 data/ 반입과
    DDL/source 내용 분류는 상류 파서(antlr)가 단독 책임진다. ``project_root`` 는 역호환
    위해 받기만 하고 **무시(inert)** — 소스를 바꾸지 않는다.
    """
    project_root: Optional[str] = None       # (역호환) 수용하되 무시 — 소스는 항상 data/source
    strategy: str                            # "framework" / "dbms"
    ddl_dir: Optional[str] = None            # (역호환) 수용하되 무시 — DDL은 항상 data/ddl
    has_xml_mapper: bool = False
    locale: str = "ko"
    datasource: Optional[str] = None         # 메타 보강 시 샘플링할 DB(MindsDB datasource). None=skip.


class ControlRequest(BaseModel):
    """``/analyze/control`` 요청 — 진행 중 분석의 일시정지/재개/중단."""
    action: Literal["pause", "resume", "stop"]


@router.post("/analyze")
async def analyze(req: AnalyzeRequest, request: Request) -> StreamingResponse:
    """분석 시작 — NDJSON 스트림 즉시 반환, 워크플로우는 백그라운드 task.

    매 호출이 독립 run. 클라이언트가 stream 을 끊으면 그 run 은 종료된다.
    API key 는 ``.env`` 의 ``ROBO_LLM_API_KEY`` 가 단일 진실.
    Neo4j 연결: ``X-Neo4j-*`` 헤더(Electron) 있으면 이 run 에 override, 없으면 SETTINGS.
    """
    if not SETTINGS.analysis_llm.api_key:
        raise HTTPException(status_code=401, detail="ROBO_LLM_API_KEY missing in .env")

    # Neo4j override 를 이 run 컨텍스트에 설정 — create_task 가 컨텍스트를 복사하므로
    # 파이프라인 전 단계의 Neo4jClient 가 그대로 본다. 헤더 없으면 None → SETTINGS 폴백.
    try:
        set_override(Neo4jOverride.from_headers(request.headers))
    except ValueError as error:
        raise HTTPException(status_code=400, detail="Invalid Neo4j override headers") from error

    try:
        pipeline = get_pipeline(req.strategy)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # 입구 통일: 입력 모드와 무관하게 표준 data/ 만 소비. 소스의 data/ 반입·내용 분류는
    # 상류 파서(antlr spec 006)가 책임진다. req.project_root 는 받되 무시(inert).
    source_root = SETTINGS.analysis_data.dir / "source"
    if not source_root.exists():
        raise HTTPException(status_code=400, detail=f"source dir not found: {source_root}")

    codebase = Codebase(
        root=source_root,
        strategy=req.strategy,  # type: ignore[arg-type]
        # Like project_root, the legacy ddl_dir is inert. Parser owns ingress and
        # Analyzer consumes only the canonical data/ddl directory.
        ddl_dir=SETTINGS.analysis_data.dir / "ddl",
        has_xml_mapper=req.has_xml_mapper,
        locale=req.locale,
        datasource=req.datasource,
    )

    stream = NDJSONStream()

    async def _run_in_background():
        # execute_run 이 error emit + stream close(finally) 를 책임짐 — 여기선 background
        # task 의 unhandled 예외만 흡수(중복 emit 금지).
        try:
            await execute_run(strategy=pipeline, codebase=codebase, stream=stream)
        except Exception as error:
            # execute_run이 원인과 스트림 오류를 이미 기록한다. task 예외를 소비했다는
            # 사실도 남겨 background task 종료가 조용히 사라지지 않게 한다.
            log_warning(
                "api.analyze_router",
                "background_run_terminated",
                "분석 background task가 오류로 종료됨 — 상세 원인은 run_failed 로그 참조",
                error=f"{type(error).__name__}: {error}",
            )

    producer = asyncio.create_task(_run_in_background())
    return StreamingResponse(
        stream.iter_with_producer(producer),
        media_type="application/x-ndjson",
    )


@router.post("/analyze/control")
async def control_analysis(req: ControlRequest) -> dict:
    """진행 중 분석 제어 — 활성 run 컨트롤러에 신호 전달.

    "동시 분석 1개" 모델 — 활성 run 이 없으면 409. 응답은 프론트 계약 그대로
    ``{success, action, status: {isPaused, isStopped}}``.
    """
    active = get_active()
    if active is None:
        raise HTTPException(status_code=409, detail="no active analysis")

    _, pause = active
    if req.action == "pause":
        pause.request_pause()
    elif req.action == "resume":
        pause.resume()
    else:  # "stop"
        pause.request_stop()

    return {"success": True, "action": req.action, "status": pause.status()}
