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

from api.request_models import PipelineControlRequest
from config.settings import settings
from service import source_analyze_service, pipeline_control_service
from util.request_helper import extract_api_key, extract_locale
from util.ndjson_stream import stream_with_error_boundary

router = APIRouter(prefix=settings.api_prefix)
logger = logging.getLogger(__name__)


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
    infer_fk_relations = body.get("inferFkRelations", False)
    custom_instructions = body.get("customInstructions") or []

    await source_analyze_service.validate_llm_api_key(api_key)

    dirs = source_analyze_service.get_analysis_directories(strategy)
    file_names = source_analyze_service.discover_analyzable_files(strategy, target, dirs)
    has_ddl = source_analyze_service.check_ddl_files_exist(dirs)

    if not file_names and not has_ddl:
        raise HTTPException(400, "분석할 소스 파일 또는 DDL이 없습니다.")

    logger.info(
        "[API] 분석 시작 | strategy=%s | files=%d | has_ddl=%s | datasource=%s | infer_fk=%s",
        strategy, len(file_names), has_ddl, datasource_name or "(none)", infer_fk_relations,
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
                infer_fk_relations=infer_fk_relations,
                custom_instructions=custom_instructions,
            )
        ),
        media_type="application/x-ndjson",
    )


@router.get("/pipeline/status")
async def get_pipeline_status():
    """파이프라인 상태 조회"""
    return pipeline_control_service.get_pipeline_status()


@router.get("/pipeline/phases")
async def get_pipeline_phases():
    """파이프라인 단계 정보 조회"""
    return pipeline_control_service.get_pipeline_phases_info()


@router.post("/pipeline/control")
async def control_pipeline(body: PipelineControlRequest):
    """파이프라인 제어 (pause/resume/stop)"""
    logger.info("[API] 파이프라인 제어 | action=%s", body.action)
    return await pipeline_control_service.control_pipeline_action(body.action)
