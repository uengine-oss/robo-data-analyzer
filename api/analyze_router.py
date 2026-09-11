"""FastAPI 분석 라우터 — 분석 시작 + 진행 제어.

API key: ``.env`` 의 ``ROBO_LLM_API_KEY`` 단일 진실. 헤더 override 미지원
(``ENV.llm.api_key`` 는 import 시점에 frozen 되어 런타임 변경 불가 — 글로벌 write 위험 회피).

**대상 graph 는 예외다.** ``X-Neo4j-Database`` 를 받으면 그 run 만 그 graph 에 쓴다.
``ENV`` 를 바꾸지 않고 값을 run 으로 넘길 뿐이라 위의 "글로벌 write 위험"에 해당하지
않는다. 매 run 이 대상 graph 를 통째로 비우므로, 대상이 프로세스 환경 하나로
고정되면 **한 프로젝트를 분석했을 뿐인데 다른 프로젝트의 결과가 사라진다.**
헤더가 없으면 지금까지처럼 ``ROBO_NEO4J_DATABASE`` 를 쓴다.

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

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from analysis_pipelines import get_pipeline
from data_models.codebase import Codebase
from shared.config.env_catalog import ENV
from shared.config.workspace import current_workspace, data_dir, set_workspace
from shared.log.logger import log_info
from shared.log.ndjson_stream import NDJSONStream
from shared.workflow.executor import execute_run
from shared.workflow.run_registry import get_active


router = APIRouter(prefix="/robo")


class AnalyzeRequest(BaseModel):
    """``/analyze`` 요청 모델.

    가공 식별자(``project_name``) + catalog 관련 인자 모두 폐기 — 진실은
    ``project_root`` 경로뿐. catalog 서비스 정보는 운영 영역 (``ROBO_CATALOG_*`` env).

    ``project_root`` 미전달(None) = 업로드 모드 → ``ENV.data.dir/source`` 기본.
    값 전달 = 경로 모드(Electron) → 그 로컬 폴더를 직접 분석.
    """
    project_root: Optional[str] = None
    strategy: str                            # "framework" / "dbms"
    # 대상 graph. 헤더로도 받는다(헤더 우선) — 본문을 못 고치는 호출자를 위해 둘 다 연다.
    database: Optional[str] = None
    ddl_dir: Optional[str] = None
    has_xml_mapper: bool = False
    locale: str = "ko"


class ControlRequest(BaseModel):
    """``/analyze/control`` 요청 — 진행 중 분석의 일시정지/재개/중단."""
    action: Literal["pause", "resume", "stop"]


def _target_database(request: Request, req: "AnalyzeRequest") -> Optional[str]:
    """이 run 이 쓸 graph. 없으면 None → ``ENV`` 폴백.

    ``system`` 은 거부한다 — 실수로 그리로 향하면 wipe 가 저장소 메타를 지운다.
    """
    value = (request.headers.get("x-neo4j-database") or req.database or "").strip()
    if not value:
        return None
    if value.lower() == "system":
        raise HTTPException(status_code=400, detail="system database is forbidden")
    return value


@router.post("/analyze")
async def analyze(request: Request, req: AnalyzeRequest) -> StreamingResponse:
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

    # **작업 폴더를 프로젝트마다 가른다.** 예전에는 서버에 하나라, 뒤에 올린
    # 사람의 소스를 앞사람이 분석했다 — 자기 프로젝트 graph 에 남의 코드가 들어가고
    # 오류는 안 난다. 대상 graph 와 **같은 값**으로 가르고, 파서도 같은 헤더로 같은
    # 자리를 쓴다. 값이 없으면 예전 자리 그대로다.
    set_workspace(_target_database(request, req))

    # project_root 미전달 = 업로드 모드 → 이 프로젝트의 source. 전달 = 경로 모드.
    root_str = req.project_root or str(data_dir() / "source")
    project_root = Path(root_str)
    if not project_root.exists():
        # **작업 폴더가 프로젝트마다 갈린 뒤로, "아직 안 올렸다"가 흔한 경우가 됐다.**
        # 예전에는 공유 폴더에 누군가 올려 둔 것이 있어 그냥 돌았다 — 그게 남의
        # 코드를 분석하던 원인이다. 이제는 막는 게 맞지만, 경로만 던지면 사람이
        # 무엇을 해야 할지 알 수 없다.
        if current_workspace():
            raise HTTPException(
                status_code=400,
                detail=("이 프로젝트에 올린 소스가 없습니다. 먼저 소스를 업로드한 뒤 "
                        f"분석을 시작하세요. (작업 폴더: {root_str})"),
            )
        raise HTTPException(status_code=400, detail=f"project_root not found: {root_str}")

    # DDL 도 같은 자리에서 찾는다 — 안 그러면 TABLE 이 조용히 0건이 된다.
    ddl_dir = Path(req.ddl_dir) if req.ddl_dir else (data_dir() / "ddl")
    if not ddl_dir.exists():
        ddl_dir = None

    codebase = Codebase(
        root=project_root,
        strategy=req.strategy,  # type: ignore[arg-type]
        ddl_dir=ddl_dir,
        has_xml_mapper=req.has_xml_mapper,
        locale=req.locale,
    )

    target_db = _target_database(request, req)

    log_info("run", "workspace_scoped", workspace=current_workspace() or "(없음)",
             root=str(project_root), ddl=str(ddl_dir) if ddl_dir else "(없음)")

    stream = NDJSONStream()

    async def _run_in_background():
        # execute_run 이 error emit + stream close(finally) 를 책임짐 — 여기선 background
        # task 의 unhandled 예외만 흡수(중복 emit 금지).
        try:
            await execute_run(strategy=pipeline, codebase=codebase, stream=stream,
                              database=target_db)
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
