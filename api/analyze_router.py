"""FastAPI 분석 라우터 — 분석 시작 + 진행 제어.

API key: ``.env`` 의 ``ROBO_LLM_API_KEY`` 단일 진실. 헤더 override 미지원
(``ENV.llm.api_key`` 는 import 시점에 frozen 되어 런타임 변경 불가 — 글로벌 write 위험 회피).

엔드포인트 2개::

    POST /robo/analyze          — 분석 시작 (NDJSON 스트림 반환)
    POST /robo/analyze/control  — 진행 제어 {action: pause|resume|stop}

진행 상태는 NDJSON 스트림 단일 채널(폴링 없음). 제어는 "동시 분석 1개" 모델 —
``run_registry`` 의 활성 run 컨트롤러에 신호를 전달한다(payload 에 run_id 없음).
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Literal, Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from analysis_pipelines import get_pipeline
from data_models.codebase import Codebase
from shared.config.env_catalog import ENV
from shared.log.ndjson_stream import NDJSONStream
from shared.workflow.executor import execute_run
from shared.workflow.run_registry import get_active


router = APIRouter(prefix="/robo")


class AnalyzeRequest(BaseModel):
    """``/analyze`` 요청 모델.

    가공 식별자(``project_name``) + catalog 관련 인자 모두 폐기 — 진실은
    ``project_root`` 경로뿐. catalog 서비스 정보는 운영 영역 (``ROBO_CATALOG_*`` env).
    """
    project_root: str
    strategy: str                            # "framework" / "dbms"
    ddl_dir: Optional[str] = None
    has_xml_mapper: bool = False
    locale: str = "ko"


class ControlRequest(BaseModel):
    """``/analyze/control`` 요청 — 진행 중 분석의 일시정지/재개/중단."""
    action: Literal["pause", "resume", "stop"]


@router.post("/analyze")
async def analyze(req: AnalyzeRequest) -> StreamingResponse:
    """분석 시작 — NDJSON 스트림 즉시 반환, 워크플로우는 백그라운드 task.

    매 호출이 독립 run. 클라이언트가 stream 을 끊으면 그 run 은 종료된다.
    API key 는 ``.env`` 의 ``ROBO_LLM_API_KEY`` 가 단일 진실.
    """
    if not ENV.llm.api_key:
        raise HTTPException(status_code=401, detail="ROBO_LLM_API_KEY missing in .env")

    try:
        pipeline = get_pipeline(req.strategy)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    project_root = Path(req.project_root)
    if not project_root.exists():
        raise HTTPException(status_code=400, detail=f"project_root not found: {req.project_root}")

    codebase = Codebase(
        root=project_root,
        strategy=req.strategy,  # type: ignore[arg-type]
        ddl_dir=Path(req.ddl_dir) if req.ddl_dir else None,
        has_xml_mapper=req.has_xml_mapper,
        locale=req.locale,
    )

    stream = NDJSONStream()

    async def _run_in_background():
        # execute_run 이 error emit + stream close(finally) 를 책임짐 — 여기선 background
        # task 의 unhandled 예외만 흡수(중복 emit 금지).
        try:
            await execute_run(strategy=pipeline, codebase=codebase, stream=stream)
        except Exception:
            pass

    asyncio.create_task(_run_in_background())
    return StreamingResponse(stream.iter(), media_type="application/x-ndjson")


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
