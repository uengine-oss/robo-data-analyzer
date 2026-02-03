"""ROBO Analyzer API 라우터

엔드포인트:
- POST /robo/analyze/      : 소스 파일 분석 → Neo4j 그래프 생성
- POST /robo/detect-types/ : 파일 내용 기반 타입 자동 감지
- GET /robo/check-data/    : Neo4j에 기존 데이터 존재 여부 확인
- DELETE /robo/data/       : 사용자 데이터 전체 삭제
- GET /robo/lineage/       : 데이터 리니지 그래프 조회
- POST /robo/lineage/analyze/ : ETL 코드에서 리니지 추출
- GET /robo/schema/tables  : 테이블 목록 조회 (Neo4j)
- 기타 스키마/파이프라인/DW API
"""

import logging
from typing import Optional, List

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from api.request_models import (
    FileContent,
    DetectTypesRequest,
    LineageAnalyzeRequest,
    LineageGraphResponse,
    SchemaTableInfo,
    SchemaColumnInfo,
    SchemaRelationshipInfo,
    AddRelationshipRequest,
    SemanticSearchRequest,
    ProcedureReferenceInfo,
    StatementSummaryInfo,
    PipelineControlRequest,
    TableDescriptionUpdateRequest,
    ColumnDescriptionUpdateRequest,
    VectorizeRequest,
    DWStarSchemaRequest,
)
from config.settings import settings
from service import (
    source_analyze_service,
    graph_query_service,
    schema_manage_service,
    data_lineage_service,
    pipeline_control_service,
    dw_schema_service,
)
from util.request_helper import extract_api_key, extract_locale
from util.stream_event import build_error_body, stream_with_error_boundary


router = APIRouter(prefix=settings.api_prefix)
logger = logging.getLogger(__name__)


# =============================================================================
# 파일 타입 감지 API
# =============================================================================

@router.post("/detect-types/")
async def detect_file_types(request: DetectTypesRequest):
    """파일 내용을 분석하여 소스 코드 타입 자동 감지"""
    logger.info("[API] 파일 타입 감지 요청 | files=%d", len(request.files))
    
    try:
        files_data = [(f.fileName, f.content) for f in request.files]
        result = source_analyze_service.detect_source_file_types(files_data)
        
        logger.info(
            "[API] 파일 타입 감지 완료 | total=%d | strategy=%s | target=%s",
            result["summary"]["total"],
            result["summary"]["suggestedStrategy"],
            result["summary"]["suggestedTarget"],
        )
        
        return result
    except Exception as e:
        logger.error("[API] 파일 타입 감지 실패 | error=%s", e)
        raise HTTPException(500, f"파일 타입 감지 실패: {e}")


# =============================================================================
# 분석 API
# =============================================================================

@router.post("/analyze/")
async def analyze_source_code(request: Request):
    """소스 파일을 분석하여 Neo4j 그래프 데이터 생성"""
    body = await request.json()
    
    api_key = extract_api_key(request)
    locale = extract_locale(request)
    strategy = (body.get("strategy") or "framework").strip().lower()
    target = (body.get("target") or "java").strip().lower()
    name_case = (body.get("nameCase") or "original").strip().lower()
    
    # API 키 검증
    await source_analyze_service.validate_llm_api_key(api_key)
    
    # 분석 대상 파일 탐색
    dirs = source_analyze_service.get_analysis_directories(strategy)
    file_names = source_analyze_service.discover_analyzable_files(strategy, dirs)
    has_ddl = source_analyze_service.check_ddl_files_exist(dirs)
    
    if not file_names and not has_ddl:
        raise HTTPException(400, "분석할 소스 파일 또는 DDL이 없습니다.")

    logger.info(
        "[API] 분석 시작 | strategy=%s | files=%d | has_ddl=%s",
        strategy, len(file_names), has_ddl,
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
            )
        ),
        media_type="application/x-ndjson",
    )


# =============================================================================
# 그래프 데이터 API
# =============================================================================

@router.get("/check-data/")
async def check_existing_data():
    """Neo4j에 기존 데이터 존재 여부 확인"""
    logger.info("[API] 데이터 존재 확인 요청")
    try:
        return await graph_query_service.check_graph_data_exists()
    except Exception as e:
        logger.error("[API] 데이터 확인 실패 | error=%s", e)
        raise HTTPException(500, build_error_body(e))


@router.get("/graph/")
async def get_graph_data():
    """Neo4j에서 기존 그래프 데이터 조회"""
    logger.info("[API] 그래프 데이터 조회")
    try:
        result = await graph_query_service.fetch_graph_data()
        logger.info(
            "[API] 그래프 데이터 조회 완료 | nodes=%d | relationships=%d",
            len(result["Nodes"]), len(result["Relationships"])
        )
        return result
    except Exception as e:
        logger.error("[API] 그래프 조회 실패 | error=%s", e)
        raise HTTPException(500, build_error_body(e))


@router.get("/graph/related-tables/{table_name}")
async def get_related_tables(table_name: str):
    """특정 테이블과 연결된 모든 테이블 조회"""
    logger.info("[API] 관련 테이블 조회 요청 | table=%s", table_name)
    try:
        result = await graph_query_service.fetch_related_tables(table_name)
        logger.info("[API] 관련 테이블 조회 완료 | table=%s | tables=%d | rels=%d", 
                    table_name, len(result["tables"]), len(result["relationships"]))
        return result
    except Exception as e:
        logger.error("[API] 관련 테이블 조회 실패 | table=%s | error=%s", table_name, e)
        raise HTTPException(500, build_error_body(e))


@router.delete("/delete/")
async def delete_user_data(include_files: bool = False):
    """사용자 데이터 삭제"""
    logger.info("[API] 데이터 삭제 요청 | include_files=%s", include_files)
    try:
        result = await graph_query_service.delete_graph_data(include_files)
        logger.info("[API] 데이터 삭제 완료 | include_files=%s", include_files)
        return result
    except Exception as e:
        logger.error("[API] 데이터 삭제 실패 | error=%s", e)
        raise HTTPException(500, build_error_body(e))


# =============================================================================
# 데이터 리니지 API
# =============================================================================

@router.get("/lineage/")
async def get_lineage_graph():
    """데이터 리니지 그래프 조회"""
    logger.info("[API] 리니지 조회 요청")
    try:
        result = await data_lineage_service.fetch_lineage_graph()
        logger.info("[API] 리니지 조회 완료 | nodes=%d | edges=%d",
                    len(result["nodes"]), len(result["edges"]))
        return result
    except Exception as e:
        logger.error("[API] 리니지 조회 실패 | error=%s", e)
        raise HTTPException(500, build_error_body(e))


@router.post("/lineage/analyze/")
async def analyze_lineage(body: LineageAnalyzeRequest):
    """ETL 코드에서 데이터 리니지 추출"""
    logger.info("[API] 리니지 분석 요청 | file=%s", body.fileName)
    try:
        result = await data_lineage_service.analyze_sql_lineage(
            sql_content=body.sqlContent,
            file_name=body.fileName,
            dbms=body.dbms,
        )
        logger.info("[API] 리니지 분석 완료 | lineages=%d", len(result["lineages"]))
        return result
    except Exception as e:
        logger.error("[API] 리니지 분석 실패 | error=%s", e)
        raise HTTPException(500, build_error_body(e))


# =============================================================================
# 스키마 API
# =============================================================================

@router.post("/schema/semantic-search")
async def semantic_search_tables(request: Request, body: SemanticSearchRequest):
    """시멘틱 검색: 테이블 설명의 의미적 유사도 기반 검색"""
    api_key = request.headers.get("X-API-Key") or settings.llm.api_key
    logger.info("[API] 시멘틱 검색 요청 | query=%s", body.query[:50])
    try:
        result = await schema_manage_service.search_tables_by_semantic(
            query=body.query,
            limit=body.limit,
            api_key=api_key
        )
        logger.info("[API] 시멘틱 검색 완료 | results=%d", len(result))
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error("[API] 시멘틱 검색 실패 | error=%s", e)
        raise HTTPException(500, build_error_body(e))


@router.get("/schema/tables", response_model=List[SchemaTableInfo])
async def list_schema_tables(search: Optional[str] = None, schema: Optional[str] = None, limit: int = 100):
    """테이블 목록 조회"""
    logger.info("[API] 테이블 목록 조회")
    try:
        records = await schema_manage_service.fetch_schema_tables(search, schema, limit)
        tables = [
            SchemaTableInfo(
                name=r["name"],
                table_schema=r["schema"] or "",
                datasource=r.get("datasource") or "",
                description=r["description"] or "",
                description_source=r.get("description_source") or "",
                analyzed_description=r.get("analyzed_description") or "",
                column_count=r["column_count"] or 0
            )
            for r in records
        ]
        logger.info("[API] 테이블 목록 조회 완료 | count=%d", len(tables))
        return tables
    except Exception as e:
        logger.error("[API] 테이블 목록 조회 실패 | error=%s", e)
        raise HTTPException(500, build_error_body(e))


@router.get("/schema/tables/{table_name}/columns", response_model=List[SchemaColumnInfo])
async def get_table_columns(table_name: str, schema: str = ""):
    """테이블 컬럼 목록 조회"""
    logger.info("[API] 컬럼 조회 | table=%s", table_name)
    try:
        records = await schema_manage_service.fetch_table_columns(table_name, schema)
        columns = [
            SchemaColumnInfo(
                name=r["name"],
                table_name=r["table_name"],
                dtype=r["dtype"] or "",
                nullable=r.get("nullable", True),
                description=r.get("description") or "",
                description_source=r.get("description_source") or "",
                analyzed_description=r.get("analyzed_description") or ""
            )
            for r in records
        ]
        return columns
    except Exception as e:
        logger.error("[API] 컬럼 조회 실패 | error=%s", e)
        raise HTTPException(500, build_error_body(e))


@router.get("/schema/relationships", response_model=List[SchemaRelationshipInfo])
async def list_schema_relationships():
    """테이블 관계 목록 조회"""
    logger.info("[API] 관계 조회")
    try:
        records = await schema_manage_service.fetch_schema_relationships()
        return [
            SchemaRelationshipInfo(
                from_table=r["from_table"],
                from_schema=r.get("from_schema") or "",
                from_column=r.get("from_column") or "",
                to_table=r["to_table"],
                to_schema=r.get("to_schema") or "",
                to_column=r.get("to_column") or "",
                relationship_type=r.get("relationship_type") or "FK_TO_TABLE",
                description=r.get("description") or ""
            )
            for r in records
        ]
    except Exception as e:
        logger.error("[API] 관계 조회 실패 | error=%s", e)
        raise HTTPException(500, build_error_body(e))


@router.post("/schema/relationships")
async def add_schema_relationship(body: AddRelationshipRequest):
    """테이블 관계 추가"""
    logger.info("[API] 관계 추가 | %s -> %s", body.from_table, body.to_table)
    try:
        return await schema_manage_service.create_schema_relationship(
            from_table=body.from_table,
            from_schema=body.from_schema,
            from_column=body.from_column,
            to_table=body.to_table,
            to_schema=body.to_schema,
            to_column=body.to_column,
            relationship_type=body.relationship_type,
            description=body.description
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error("[API] 관계 추가 실패 | error=%s", e)
        raise HTTPException(500, build_error_body(e))


@router.delete("/schema/relationships")
async def remove_schema_relationship(
    from_table: str,
    from_column: str,
    to_table: str,
    to_column: str
):
    """테이블 관계 삭제"""
    logger.info("[API] 관계 삭제 | %s.%s -> %s.%s", from_table, from_column, to_table, to_column)
    try:
        return await schema_manage_service.delete_schema_relationship(
            from_table, from_column, to_table, to_column
        )
    except Exception as e:
        logger.error("[API] 관계 삭제 실패 | error=%s", e)
        raise HTTPException(500, build_error_body(e))


@router.get("/schema/tables/{table_name}/references")
async def get_table_references(table_name: str, schema: str = "", column_name: Optional[str] = None):
    """테이블 또는 컬럼이 참조된 프로시저 목록 조회"""
    logger.info("[API] 테이블 참조 조회 | table=%s", table_name)
    try:
        records = await schema_manage_service.fetch_table_references(table_name, schema, column_name)
        return {"references": records}
    except Exception as e:
        logger.error("[API] 테이블 참조 조회 실패 | error=%s", e)
        raise HTTPException(500, build_error_body(e))


@router.get("/schema/procedures/{procedure_name}/statements")
async def get_procedure_statements(procedure_name: str, file_directory: Optional[str] = None):
    """프로시저의 모든 Statement와 AI 설명 조회"""
    logger.info("[API] Statement 조회 | procedure=%s", procedure_name)
    try:
        records = await schema_manage_service.fetch_procedure_statements(procedure_name, file_directory)
        return {"statements": records}
    except Exception as e:
        logger.error("[API] Statement 조회 실패 | error=%s", e)
        raise HTTPException(500, build_error_body(e))


# =============================================================================
# 파이프라인 제어 API
# =============================================================================

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


# =============================================================================
# 스키마 편집 API
# =============================================================================

@router.put("/schema/tables/{table_name}/description")
async def update_table_description(request: Request, table_name: str, body: TableDescriptionUpdateRequest):
    """테이블 설명 업데이트"""
    api_key = request.headers.get("X-API-Key") or settings.llm.api_key
    logger.info("[API] 테이블 설명 업데이트 | table=%s", table_name)
    try:
        return await schema_manage_service.update_table_description(
            table_name=table_name,
            schema=body.schema,
            description=body.description,
            api_key=api_key
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error("[API] 테이블 설명 업데이트 실패 | error=%s", e)
        raise HTTPException(500, build_error_body(e))


@router.put("/schema/tables/{table_name}/columns/{column_name}/description")
async def update_column_description(
    request: Request,
    table_name: str,
    column_name: str,
    body: ColumnDescriptionUpdateRequest
):
    """컬럼 설명 업데이트"""
    api_key = request.headers.get("X-API-Key") or settings.llm.api_key
    logger.info("[API] 컬럼 설명 업데이트 | table=%s | column=%s", table_name, column_name)
    try:
        return await schema_manage_service.update_column_description(
            table_name=table_name,
            table_schema=body.table_schema,
            column_name=column_name,
            description=body.description,
            api_key=api_key
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error("[API] 컬럼 설명 업데이트 실패 | error=%s", e)
        raise HTTPException(500, build_error_body(e))


@router.post("/schema/vectorize")
async def vectorize_schema(request: Request, body: VectorizeRequest):
    """전체 스키마 벡터화"""
    api_key = request.headers.get("X-API-Key") or settings.llm.api_key
    logger.info("[API] 스키마 벡터화 요청")
    try:
        return await schema_manage_service.vectorize_schema_tables(
            db_name=body.db_name,
            schema=body.schema,
            include_tables=body.include_tables,
            include_columns=body.include_columns,
            reembed_existing=body.reembed_existing,
            batch_size=body.batch_size,
            api_key=api_key
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error("[API] 스키마 벡터화 실패 | error=%s", e)
        raise HTTPException(500, build_error_body(e))


# =============================================================================
# DW 스타스키마 API
# =============================================================================

@router.post("/schema/dw-tables")
async def register_dw_star_schema(request: Request, body: DWStarSchemaRequest):
    """DW 스타스키마 등록"""
    api_key = request.headers.get("X-API-Key") or settings.llm.api_key
    logger.info("[API] DW 스타스키마 등록 | cube=%s", body.cube_name)
    try:
        return await dw_schema_service.register_star_schema(
            cube_name=body.cube_name,
            db_name=body.db_name,
            dw_schema=body.dw_schema,
            fact_table=body.fact_table.model_dump(),
            dimensions=[d.model_dump() for d in body.dimensions],
            create_embeddings=body.create_embeddings,
            api_key=api_key
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error("[API] DW 스타스키마 등록 실패 | error=%s", e)
        raise HTTPException(500, build_error_body(e))


@router.delete("/schema/dw-tables/{cube_name}")
async def delete_dw_star_schema(cube_name: str, schema: str = "dw", db_name: str = "postgres"):
    """DW 스타스키마 삭제"""
    logger.info("[API] DW 스타스키마 삭제 | cube=%s", cube_name)
    try:
        return await dw_schema_service.delete_star_schema(cube_name, schema, db_name)
    except Exception as e:
        logger.error("[API] DW 스타스키마 삭제 실패 | error=%s", e)
        raise HTTPException(500, build_error_body(e))

