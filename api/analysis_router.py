"""ROBO Analyzer API 라우터

엔드포인트:
  POST /analyze/           소스 파일 분석 → Neo4j 그래프 생성
  GET  /pipeline/status    파이프라인 상태
  GET  /pipeline/phases    파이프라인 단계 정보
  POST /pipeline/control   파이프라인 제어
"""

import logging

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from analyzer.common.pipeline_control import pipeline_controller
from config.settings import settings
from service import source_analyze_service
from util.stream_emitter import stream_with_error_boundary

router = APIRouter(prefix=settings.api_prefix)
logger = logging.getLogger(__name__)


def extract_api_key(request: Request, missing_status: int = 401) -> str:
    """API 키 추출 (헤더에서만 추출, 환경변수 폴백 없음)"""
    api_key = (
        request.headers.get("OpenAI-Api-Key") or
        request.headers.get("Anthropic-Api-Key")
    )
    if not api_key:
        raise HTTPException(
            missing_status,
            "요청 헤더 누락: OpenAI-Api-Key 또는 Anthropic-Api-Key 헤더가 필요합니다."
        )
    return api_key


def extract_locale(request: Request) -> str:
    """Accept-Language 헤더에서 로케일 추출"""
    return request.headers.get("Accept-Language", "ko")


class PipelineControlRequest(BaseModel):
    """파이프라인 제어 요청"""
    action: str  # "pause" | "resume" | "stop"


@router.post("/analyze/")
async def analyze_source_code(request: Request):
    """소스 파일을 분석하여 Neo4j 그래프 데이터 생성 (NDJSON 스트리밍 응답)"""
    body = await request.json()
    api_key = extract_api_key(request)
    locale = extract_locale(request)

    strategy = body.get("strategy")
    if not strategy:
        raise HTTPException(400, "strategy 파라미터가 필요합니다. (framework 또는 dbms)")
    strategy = strategy.strip().lower()

    target = body.get("target")
    if not target:
        raise HTTPException(400, "target 파라미터가 필요합니다. (java, oracle, postgres 등)")
    target = target.strip().lower()

    name_case = (body.get("nameCase") or "original").strip().lower()
    datasource_name = (body.get("datasourceName") or "").strip()
    if not datasource_name:
        raise HTTPException(
            400,
            "datasourceName 필수 — catalog API 호출 시 MindsDB datasource 이름 지정 필요",
        )

    await source_analyze_service.validate_llm_api_key(api_key)

    dirs = source_analyze_service.get_analysis_directories(strategy)
    file_names = source_analyze_service.discover_analyzable_files(strategy, target, dirs)
    has_ddl = source_analyze_service.check_ddl_files_exist(dirs)

    if not file_names and not has_ddl:
        raise HTTPException(400, "분석할 소스 파일 또는 DDL이 없습니다.")

    logger.info(
        "[API] 분석 시작 | strategy=%s | files=%d | has_ddl=%s | datasource=%s",
        strategy, len(file_names), has_ddl, datasource_name or "(none)",
    )

    return StreamingResponse(
        stream_with_error_boundary(
            source_analyze_service.run_source_analysis(
                file_names=file_names,
                api_key=api_key,
                locale=locale,
                strategy=strategy,
                target=target,
                name_case=name_case,
                datasource_name=datasource_name,
            )
        ),
        media_type="application/x-ndjson",
    )


@router.get("/pipeline/status")
async def get_pipeline_status():
    """파이프라인 상태 조회"""
    return pipeline_controller.get_status()


@router.get("/pipeline/phases")
async def get_pipeline_phases():
    """파이프라인 단계 정보 조회"""
    return pipeline_controller.get_phases_info()


@router.post("/pipeline/control")
async def control_pipeline(body: PipelineControlRequest):
    """파이프라인 제어 (pause/resume/stop)"""
    logger.info("[API] 파이프라인 제어 | action=%s", body.action)
    action_lower = body.action.lower()

    if action_lower == "pause":
        pipeline_controller.pause()
        return {"message": "분석 일시정지됨"}
    elif action_lower == "resume":
        pipeline_controller.resume()
        return {"message": "분석 재개됨"}
    elif action_lower == "stop":
        pipeline_controller.stop()
        return {"message": "분석 중단됨"}
    else:
        raise HTTPException(400, f"지원하지 않는 액션: {body.action}")
