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
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from pipeline.pipeline_strategies import get_pipeline
from pipeline.models.analysis_meta import Codebase
from shared.config.app_settings import SETTINGS
from shared.config.workspace import current_workspace, data_dir, set_workspace
from shared.events.ndjson_event_stream import NDJSONStream
from shared.events.runtime_logger import log_warning
from graph.analysis_lock import AnalysisLock, AnalysisLockBusy
from graph.connection_context import Neo4jOverride, set_override
from pipeline.execution.run_executor import execute_run
from pipeline.execution.run_registry import get_active


router = APIRouter(prefix="/robo")


def _holder_from(request: Request) -> str:
    """락을 잡은 사람을 사람이 읽을 수 있게 표시한다.

    거절 메시지에 그대로 들어가므로, 누구를 기다려야 하는지 알 수 있어야 한다.
    인증이 꺼진 구성에서는 `X-User-*` 가 없을 수 있어 여러 단을 둔다.
    """
    headers = request.headers
    for name in ("x-user-name", "x-user-id", "x-neo4j-user"):
        value = (headers.get(name) or "").strip()
        if value:
            return value
    client = request.client.host if request.client else None
    return client or "알 수 없는 사용자"


class AnalyzeRequest(BaseModel):
    """``/analyze`` 요청 모델.

    catalog **접속 설정**(base_url/enabled)은 운영 영역 (``ROBO_CATALOG_*`` 환경변수).
    단 ``datasource`` ("이번 분석에서 어느 DB 를 샘플링")는 분석 입력값.

    입구 통일: 분석기는 항상 표준
    ``SETTINGS.analysis_data.dir/{source, ddl, analysis}`` 만 소비한다. 소스의 data/ 반입과
    DDL/source 내용 분류는 상류 Parser가 단독 책임진다. 호출자가 별도 경로를 보내면
    조용히 무시하지 않고 요청 계약 오류로 거부한다.
    """
    model_config = ConfigDict(extra="forbid")

    strategy: str                            # "framework" / "dbms"
    locale: str = "ko"
    selected_source_ids: list[str] = Field(default_factory=list)
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
        override = Neo4jOverride.from_headers(request.headers)
    except ValueError as error:
        raise HTTPException(status_code=400, detail="Invalid Neo4j override headers") from error
    set_override(override)

    # **작업 폴더를 프로젝트마다 가른다.** 예전에는 서버에 하나라, 뒤에 올린 사람의
    # 소스를 앞사람이 분석했다 — 자기 프로젝트 graph 에 남의 코드가 들어가고 오류는
    # 안 난다. 대상 graph 와 **같은 값**으로 가르고, 파서도 같은 헤더로 같은 자리를
    # 쓴다(`antlr-code-parser` 의 `intake/WorkspaceScope.java`). 값이 없으면
    # (CLI·브라우저 기본) 예전 자리 그대로다.
    set_workspace(override.database if override else None)

    try:
        pipeline = get_pipeline(req.strategy)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # 입구 통일: 입력 모드와 무관하게 표준 data/ 만 소비. 소스의 data/ 반입·내용 분류는
    # 상류 Parser가 책임진다. **다만 그 data/ 는 이 run 의 프로젝트 것이다.**
    corpus_root = data_dir()
    source_root = corpus_root / "source"
    if not source_root.exists():
        # **작업 폴더가 프로젝트마다 갈린 뒤로 "아직 안 올렸다"가 흔한 경우가 됐다.**
        # 예전에는 공용 폴더에 누군가 올려 둔 것이 있어 그냥 돌았다 — 그게 남의 코드를
        # 분석하던 원인이다. 이제는 막는 게 맞지만, 경로만 던지면 사람이 무엇을 해야
        # 할지 알 수 없다.
        if current_workspace():
            raise HTTPException(
                status_code=400,
                detail=("이 프로젝트에 올린 소스가 없습니다. 먼저 소스를 업로드한 뒤 "
                        f"분석을 시작하세요. (작업 폴더: {source_root})"),
            )
        raise HTTPException(status_code=400, detail=f"source dir not found: {source_root}")

    codebase = Codebase(
        root=source_root,
        strategy=req.strategy,  # type: ignore[arg-type]
        # DDL 도 **같은 뿌리에서** 찾는다 — 어긋나면 TABLE 이 조용히 0건이 된다.
        ddl_dir=corpus_root / "ddl",
        locale=req.locale,
        datasource=req.datasource,
        corpus_root=corpus_root,
        selected_source_ids=tuple(req.selected_source_ids),
    )

    # **같은 분석 graph 를 두 사람이 동시에 덮어쓰지 못하게 막는다.**
    #
    # `run_registry` 의 "동시 분석 1개" 는 이 컨테이너 안에서만 유효하다. 각 PC 가
    # 자기 analyzer 를 띄우는 구성에서 DB 를 공유하면 그 락들은 서로를 보지 못하고,
    # 분석이 대상 graph 를 덮어쓰므로 **뒤에 시작한 사람이 앞사람 결과를 지운다.**
    # 오류는 나지 않는다 — 그래서 여기서 막는다(`graph/analysis_lock.py`).
    lock = AnalysisLock(run_id=uuid4().hex, holder=_holder_from(request))
    try:
        await lock.acquire()
    except AnalysisLockBusy as busy:
        # 409 로 준다. 스트림을 열고 그 안에서 알리면 화면이 "분석 시작" 을 먼저
        # 보여 준 뒤 실패하므로, 사람이 자기 실행이 돌았다고 착각한다.
        raise HTTPException(status_code=409, detail=str(busy)) from busy

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
        finally:
            # **어떤 경로로 끝나도 놓는다.** 클라이언트가 스트림을 끊어 이 task 가
            # 취소되는 경우까지 포함한다 — 안 놓으면 TTL(3분) 동안 다음 사람이 막힌다.
            await lock.release()

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
