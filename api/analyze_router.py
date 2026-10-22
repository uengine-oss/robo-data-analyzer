"""FastAPI 분석 라우터 — 분석 시작 단일 엔드포인트.

API key: ``.env`` 의 공급자별 표준 키(``OPENAI_API_KEY`` 등) 단일 진실. 헤더 override 미지원
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
    """``/analyze`` 요청 모델.

    **프론트가 실제로 보내는 것을 받는다.** robo-data-frontend 는
    ``{strategy, target, nameCase, datasource, inferFkRelations}`` 를 보내고
    ``project_name`` / ``project_root`` 를 **어떤 경로로도 보내지 않는다.** 둘을 필수로
    두면 모든 업로드가 422 로 끝나고, 화면에는 "ROBO 백엔드 서비스가 동작하지 않습니다"
    로만 보여 원인이 계약 불일치인 줄 알 방법이 없다.

    그래서 둘 다 선택 항목으로 두고 여기서 채운다:

    - ``project_name`` ← ``datasource`` → ``project_root`` 의 마지막 조각 → ``"project"``.
      분석기가 이 값을 Neo4j 의 ``datasource_name`` 으로 박으므로(Project.name),
      사용자가 고른 데이터소스 이름이 오는 것이 맞다.
    - ``project_root`` ← 반입된 소스 디렉터리(``ROBO_DATA_DIR/source``,
      기본 ``~/ontologic/source``).
      업로드 경로에서는 파일이 이미 거기 있고, 프론트는 경로를 알지 못한다.

    이전에는 데스크톱 셸이 프록시에서 본문을 번역해 우회했다
    (``desktop/src/main/webserver.js`` ``translateAnalyzeBody``). 그 심은 Electron
    메인 프로세스 상태에 의존해 서버·브라우저 배포에서는 쓸 수 없었고, 그래서 같은
    요청이 데스크톱에서만 통했다. 계약을 여기서 맞추면 세 배포가 같은 길을 쓴다.
    """
    strategy: str                            # "framework" / "dbms"
    project_name: Optional[str] = None
    project_root: Optional[str] = None
    datasource: Optional[str] = None
    ddl_dir: Optional[str] = None
    has_xml_mapper: bool = False
    locale: str = "ko"
    catalog_service_url: Optional[str] = None    # DBMS 만
    catalog_api_key: Optional[str] = None        # DBMS 만

    # 프론트가 함께 보내지만 분석기가 쓰지 않는 항목들. 선언해 두지 않으면 pydantic 이
    # 거부하는 것이 아니라 조용히 흘리는데, 그러면 계약이 무엇인지 코드에서 읽을 수 없다.
    target: Optional[str] = None
    nameCase: Optional[str] = None
    inferFkRelations: Optional[bool] = None

    def resolved_root(self) -> Path:
        if self.project_root:
            return Path(self.project_root)
        return Path(ENV.data.dir) / "source"

    def resolved_ddl_dir(self) -> Optional[Path]:
        """``ddl_dir`` 를 안 보내면 반입된 DDL 자리(``ROBO_DATA_DIR/ddl``)를 쓴다.

        ANTLR 파서는 업로드한 DDL 을 ``data/ddl`` 에 둔다. 브라우저 업로드 경로는 이 값을
        보내지 않아(데스크톱 셸만 프록시에서 채웠다) DDL 파싱이 ``data/source`` 를 뒤져
        테이블을 하나도 못 찾았다.
        """
        if self.ddl_dir:
            return Path(self.ddl_dir)
        if self.project_root:
            return None
        ddl = Path(ENV.data.dir) / "ddl"
        return ddl if ddl.is_dir() and any(ddl.iterdir()) else None

    def resolved_name(self) -> str:
        if self.project_name:
            return self.project_name
        if self.datasource:
            return self.datasource
        root = self.resolved_root()
        return root.name or "project"


@router.post("/analyze")
async def analyze(req: AnalyzeRequest) -> StreamingResponse:
    """분석 시작 — NDJSON 스트림 즉시 반환, 워크플로우는 백그라운드 task.

    매 호출이 독립 run. 클라이언트가 stream 을 끊으면 그 run 은 종료된다.
    API key 는 ``.env`` 의 공급자별 표준 키(``OPENAI_API_KEY`` 등)가 단일 진실.
    """
    if not ENV.llm.api_key:
        raise HTTPException(status_code=401, detail="LLM API key missing in .env (OPENAI_API_KEY / ANTHROPIC_API_KEY / OPENROUTER_API_KEY / LLM_API_KEY)")

    try:
        pipeline = get_pipeline(req.strategy)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    project_root = req.resolved_root()
    if not project_root.exists():
        raise HTTPException(
            status_code=400,
            detail=f"project_root not found: {project_root} "
                   f"(ROBO_DATA_DIR={ENV.data.dir})",
        )

    project = Project(
        name=req.resolved_name(),
        root=project_root,
        strategy=req.strategy,  # type: ignore[arg-type]
        ddl_dir=req.resolved_ddl_dir(),
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
