"""FastAPI 분석 라우터 — 분석 시작 단일 엔드포인트.

API key: ``.env`` 의 ``ROBO_LLM_API_KEY`` 단일 진실. 헤더 override 미지원
(``ENV.llm.api_key`` 는 import 시점에 frozen 되어 런타임 변경 불가 — 글로벌 write 위험 회피).

**폴링 / 컨트롤 엔드포인트 폐기 (Plan §4 M1)**:
- ``/pipeline/status`` 폴링 ★ 폐기 — 진행 상태는 NDJSON 스트림 (``/analyze``).
- ``/pipeline/control`` pause/resume/stop ★ 폐기 — 진행은 NDJSON 스트림 단일 채널.
  매 ``/analyze`` = 독립 run. resume(중단 재개)은 미지원 (프론트가 필요로 할 때 도입).

엔드포인트 1 개::

    POST /robo/analyze
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from analysis_pipelines import get_pipeline
from data_models.project_and_run import Project
from shared.config.env_catalog import ENV
from shared.logging_and_stream.ndjson_stream import NDJSONStream
from shared.workflow_runner.run_executor import execute_run


router = APIRouter(prefix="/robo")


class AnalyzeRequest(BaseModel):
    """``/analyze`` 요청 모델."""
    project_name: str
    project_root: str
    strategy: str                            # "framework" / "dbms"
    ddl_dir: Optional[str] = None
    has_xml_mapper: bool = False
    locale: str = "ko"
    catalog_service_url: Optional[str] = None    # DBMS 만
    catalog_api_key: Optional[str] = None        # DBMS 만


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

    project = Project(
        name=req.project_name,
        root=project_root,
        strategy=req.strategy,  # type: ignore[arg-type]
        ddl_dir=Path(req.ddl_dir) if req.ddl_dir else None,
        has_xml_mapper=req.has_xml_mapper,
        locale=req.locale,
        catalog_service_url=req.catalog_service_url,
        catalog_api_key=req.catalog_api_key,
    )

    stream = NDJSONStream()

    async def _run_in_background():
        try:
            await execute_run(
                strategy=pipeline,
                project=project,
                stream=stream,
            )
        except Exception as e:
            # 절대원칙 #3 — silent fail 금지. 오류 stream emit + stream 종료.
            stream.emit({"type": "error", "message": f"{type(e).__name__}: {e}"})
            await stream.close()

    asyncio.create_task(_run_in_background())
    return StreamingResponse(stream.iter(), media_type="application/x-ndjson")
