"""Phase 3.5: 메타데이터 보강 - DBMS

Text2SQL API로 샘플 데이터 조회 후 LLM으로 테이블/컬럼 설명 생성.
FK 관계 추론 포함. dbms_pipeline에서 호출.
"""

import asyncio
import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any, AsyncGenerator, Dict, List, Optional, Tuple

import aiohttp
from openai import AsyncOpenAI

from client.neo4j_client import Neo4jClient
from analyzer.strategy.base_analyzer import AnalysisStats
from config.settings import settings
from service.metadata_enrichment_service import MetadataEnrichmentService
from util.ndjson_stream import (
    emit_data,
    emit_message,
    emit_node_event,
    emit_phase_event,
    emit_relationship_event,
)
from util.text_utils import log_process

# 상수
PHASE_NUM = 3.5
PHASE_NAME = "메타데이터 보강"
DESCRIPTION_SOURCE = "sample_data_inference"
FK_PROGRESS_RANGE = (0, 50)  # FK 추론 진행률 범위
DESC_PROGRESS_RANGE = (50, 100)  # 설명 생성 진행률 범위


async def run_phase_metadata(
    pipeline: Any,
    client: Neo4jClient,
    orchestrator: Any,
    stats: AnalysisStats,
) -> AsyncGenerator[bytes, None]:
    """Phase 3.5: 메타데이터 보강 (Text2SQL 기반)
    
    1. description이 없는 테이블 목록 조회
    2. FK 관계 추론 (선택적)
    3. 샘플 데이터 조회 및 LLM 설명 생성
    4. Neo4j에 description 업데이트
    
    Args:
        pipeline: DbmsPipeline 인스턴스 (총괄 파이프라인)
        client: Neo4j 클라이언트
        orchestrator: 오케스트레이터
        stats: 분석 통계
    """
    async for chunk in _init_phase():
        yield chunk
    
    # 초기화 및 검증
    enrichment_service = None
    async for chunk in _init_enrichment_service(orchestrator, client):
        if isinstance(chunk, MetadataEnrichmentService):
            enrichment_service = chunk
        else:
            yield chunk
    
    if not enrichment_service:
        return
    
    # description 없는 테이블 조회
    tables_to_enrich = []
    async for chunk in _get_tables_without_description(pipeline, client):
        if isinstance(chunk, list):
            tables_to_enrich = chunk
        else:
            yield chunk
    
    if not tables_to_enrich:
        return
    
    # Text2SQL 서버 확인 및 처리
    async with aiohttp.ClientSession() as session:
        server_available = False
        async for chunk in _check_text2sql_server(enrichment_service, session):
            if isinstance(chunk, bool):
                server_available = chunk
            else:
                yield chunk
        
        if not server_available:
            return
        
        # FK 관계 추론 (설명 생성 전에 실행)
        fk_enabled = _is_fk_inference_enabled(orchestrator)
        if fk_enabled:
            yield emit_message(f"   🔍 FK 추론 활성화됨 (소스 테이블: {len(tables_to_enrich)}개)")
            async for chunk in _run_fk_inference(
                pipeline, client, session, enrichment_service, stats, tables_to_enrich
            ):
                yield chunk
        else:
            yield emit_message("   ⏭️ FK 추론이 비활성화되어 있습니다")
        
        # 설명 생성 및 업데이트
        async for chunk in _enrich_table_descriptions(
            pipeline, client, session, enrichment_service, stats, tables_to_enrich
        ):
            yield chunk
        
        # 완료 이벤트
        async for chunk in _complete_phase(stats):
            yield chunk


async def _init_phase() -> AsyncGenerator[bytes, None]:
    """Phase 초기화"""
    log_process("METADATA", "START", "메타데이터 보강 단계 시작", logging.INFO)
    yield emit_message(f"   🚀 [Phase {PHASE_NUM}] {PHASE_NAME} 시작...")
    yield emit_phase_event(
        phase_num=PHASE_NUM,
        phase_name=PHASE_NAME,
        status="in_progress",
        progress=0,
        details={"step": "init"},
    )


async def _init_enrichment_service(
    orchestrator: Any, client: Neo4jClient
) -> AsyncGenerator[bytes | MetadataEnrichmentService, None]:
    """Enrichment 서비스 초기화"""
    # API 키 확인
    api_key = orchestrator.api_key or settings.llm.api_key
    if not api_key:
        yield emit_message("   ⚠️ OpenAI API 키가 없어 메타데이터 보강을 건너뜁니다")
        return
    
    # Text2SQL URL 확인
    text2sql_url = settings.metadata_enrichment.text2sql_api_url
    if not text2sql_url:
        log_process("METADATA", "SKIP", "TEXT2SQL_API_URL이 설정되지 않음", logging.INFO)
        yield emit_message("   ⏭️ TEXT2SQL_API_URL이 설정되지 않아 메타데이터 보강을 건너뜁니다")
        return
    
    # OpenAI 클라이언트 생성
    try:
        openai_client = AsyncOpenAI(api_key=api_key)
    except Exception as e:
        log_process("METADATA", "ERROR", f"OpenAI 클라이언트 초기화 실패: {e}", logging.ERROR)
        yield emit_message(f"   ⚠️ OpenAI 클라이언트 초기화 실패: {str(e)[:50]}")
        return
    
    # 서비스 인스턴스 생성
    datasource_name = getattr(orchestrator, "datasource_name", "") or ""
    yield MetadataEnrichmentService(
        client=client,
        openai_client=openai_client,
        text2sql_base_url=text2sql_url,
        datasource_name=datasource_name,
    )


async def _get_tables_without_description(
    pipeline: Any, client: Neo4jClient
) -> AsyncGenerator[bytes | List[Dict[str, Any]], None]:
    """description이 없는 테이블 목록 조회"""
    yield emit_message("   🔍 [Phase 3.5-1] description이 비어있는 테이블 조회 중...")
    
    # 전체 테이블 수 조회
    total_query = """
    MATCH (t:Table)
    RETURN count(t) AS total_count
    """
    
    query = """
    MATCH (t:Table)
    WHERE t.description IS NULL 
       OR t.description = '' 
       OR t.description = 'N/A'
    RETURN t.name AS table_name, t.schema AS schema_name
    ORDER BY t.schema, t.name
    """
    
    try:
        async with pipeline._cypher_lock:
            total_results = await client.execute_queries([total_query])
            results = await client.execute_queries([query])
        
        total_count = total_results[0][0]["total_count"] if total_results and len(total_results) > 0 and len(total_results[0]) > 0 else 0
        tables = results[0] if results and len(results) > 0 else []
        
        log_process("METADATA", "QUERY", f"전체 테이블: {total_count}개, description 없는 테이블: {len(tables)}개", logging.INFO)
        
        if not tables:
            yield emit_message(f"   ✅ 모든 테이블에 description이 존재합니다 (전체: {total_count}개). 보강 불필요.")
            yield []
            return
        
        # 처음 5개 테이블 이름 표시
        sample_tables = [f"{t.get('schema_name', 'public')}.{t.get('table_name', '')}" for t in tables[:5]]
        sample_str = ", ".join(sample_tables)
        if len(tables) > 5:
            sample_str += f" 외 {len(tables) - 5}개"
        
        yield emit_message(f"   📋 description이 비어있는 테이블: {len(tables)}개 (전체: {total_count}개)")
        yield emit_message(f"      예시: {sample_str}")
        yield tables
        
    except Exception as e:
        log_process("METADATA", "ERROR", f"테이블 조회 예외: {e}", logging.ERROR, e)
        yield emit_message(f"   ⚠️ 테이블 조회 실패: {str(e)[:100]}")
        yield []


async def _check_text2sql_server(
    enrichment_service: MetadataEnrichmentService, session: aiohttp.ClientSession
) -> AsyncGenerator[bytes | bool, None]:
    """Text2SQL 서버 연결 확인"""
    yield emit_message("      🔍 Text2SQL 서버 연결 확인 중...")
    
    server_available = await enrichment_service.check_text2sql_available(session)
    
    if not server_available:
        log_process("METADATA", "SERVER_UNAVAIL", "Text2SQL 서버 사용 불가", logging.WARNING)
        yield emit_message("   ⚠️ Text2SQL 서버에 연결할 수 없습니다")
        yield emit_message("   ⏭️ 메타데이터 보강 및 FK 추론을 건너뜁니다")
        yield False
        return
    
    yield emit_message("      ✅ Text2SQL 서버 연결 확인 완료")
    yield True


def _is_fk_inference_enabled(orchestrator: Any) -> bool:
    """FK 추론 활성화 여부 확인"""
    infer_fk_from_ui = getattr(orchestrator, "infer_fk_relations", False)
    return infer_fk_from_ui or settings.metadata_enrichment.fk_inference_enabled


async def _enrich_table_descriptions(
    pipeline: Any,
    client: Neo4jClient,
    session: aiohttp.ClientSession,
    enrichment_service: MetadataEnrichmentService,
    stats: AnalysisStats,
    tables_to_enrich: List[Dict[str, Any]],
) -> AsyncGenerator[bytes, None]:
    """테이블 설명 생성 및 업데이트"""
    total_tables = len(tables_to_enrich)
    
    yield emit_message("   📊 [Phase 3.5-3] 샘플 데이터 기반 설명 생성 중...")
    yield emit_message(f"      → 보강 대상 테이블: {total_tables}개 (설명이 없는 테이블)")
    
    enriched_count = 0
    tables_updated = 0
    columns_updated = 0
    failed_tables = []  # 실패한 테이블 목록
    failure_reasons: Dict[str, int] = {}  # 실패 사유별 집계
    
    for idx, row in enumerate(tables_to_enrich):
        table_name = row.get("table_name", "")
        schema_name = row.get("schema_name", "public")
        full_table_name = f'"{schema_name}"."{table_name}"'
        
        # 진행률 업데이트 (10개마다 표시, 처음 5개는 항상 표시)
        if idx < 5 or idx % 10 == 0 or idx == total_tables - 1:
            progress = DESC_PROGRESS_RANGE[0] + int((idx / total_tables) * (DESC_PROGRESS_RANGE[1] - DESC_PROGRESS_RANGE[0]))
            yield emit_message(f"      🔄 진행: {idx+1}/{total_tables} (완료: {enriched_count}개, 실패: {len(failed_tables)}개)")
            yield emit_phase_event(
                phase_num=PHASE_NUM,
                phase_name=PHASE_NAME,
                status="in_progress",
                progress=progress,
                details={"current_table": full_table_name, "done": idx, "total": total_tables},
            )
        
        # 각 테이블 처리 시작 로그 (처음 10개는 상세 로그)
        if idx < 10:
            yield emit_message(f"      📋 [{idx+1}/{total_tables}] {full_table_name} 처리 중...")
        
        try:
            # 샘플 데이터 조회 및 설명 생성
            t_updated, c_updated = 0, 0
            failure_reason = None
            async for chunk in _process_table_description(
                pipeline, client, session, enrichment_service, table_name, schema_name, full_table_name
            ):
                if isinstance(chunk, tuple):
                    t_updated, c_updated = chunk
                elif isinstance(chunk, str):
                    # 실패 메시지 문자열인 경우
                    failure_reason = chunk
                elif isinstance(chunk, bytes):
                    # bytes인 경우 (스트리밍 데이터) - 성공한 경우의 그래프 업데이트만 전달
                    yield chunk
            
            if t_updated or c_updated:
                enriched_count += 1
                tables_updated += t_updated
                columns_updated += c_updated
                # 성공한 경우 로그 (처음 10개는 상세, 이후는 10개마다 요약)
                if idx < 10:
                    yield emit_message(f"         ✓ {full_table_name}: 설명 생성 완료 (테이블: {t_updated}개, 컬럼: {c_updated}개)")
                elif enriched_count % 10 == 0:
                    yield emit_message(f"         ✓ {enriched_count}개 테이블 설명 생성 완료")
            else:
                # 실패한 경우 항상 상세 로그
                if failure_reason:
                    failed_tables.append((full_table_name, failure_reason))
                    # 실패 사유별 집계
                    if "샘플 데이터 없음" in failure_reason:
                        failure_reasons["샘플 데이터 없음"] = failure_reasons.get("샘플 데이터 없음", 0) + 1
                    elif "LLM" in failure_reason or "응답 없음" in failure_reason:
                        failure_reasons["LLM 설명 생성 실패"] = failure_reasons.get("LLM 설명 생성 실패", 0) + 1
                    elif "컬럼 정보" in failure_reason:
                        failure_reasons["컬럼 정보 조회 실패"] = failure_reasons.get("컬럼 정보 조회 실패", 0) + 1
                    elif "Neo4j" in failure_reason:
                        failure_reasons["Neo4j 업데이트 실패"] = failure_reasons.get("Neo4j 업데이트 실패", 0) + 1
                    else:
                        failure_reasons["기타"] = failure_reasons.get("기타", 0) + 1
                    yield emit_message(f"         ✗ {full_table_name}: {failure_reason}")
                
        except Exception as e:
            log_process("METADATA", "TABLE_ERROR", f"테이블 처리 실패: {full_table_name} - {e}", logging.WARNING)
            failed_tables.append((full_table_name, f"예외 발생: {str(e)[:80]}"))
            yield emit_message(f"         ✗ {full_table_name}: 예외 발생 - {str(e)[:80]}")
            continue
    
    # 통계 업데이트
    stats.tables_enriched = getattr(stats, "tables_enriched", 0) + tables_updated
    stats.columns_enriched = getattr(stats, "columns_enriched", 0) + columns_updated
    
    # 실패한 테이블이 있으면 요약 출력 및 파일 저장
    if failed_tables:
        yield emit_message(f"   ⚠️ 처리 실패한 테이블: {len(failed_tables)}개 / 전체 {total_tables}개")
        # 실패 사유별 요약
        if failure_reasons:
            yield emit_message(f"      📊 실패 사유별 요약:")
            for reason, count in sorted(failure_reasons.items(), key=lambda x: -x[1]):
                yield emit_message(f"         • {reason}: {count}건")
        
        # 데이터가 없는 테이블 목록 추출 (샘플 데이터 없음)
        no_data_tables = [
            table_name for table_name, reason in failed_tables 
            if "샘플 데이터 없음" in reason
        ]
        
        if no_data_tables:
            yield emit_message(f"      📋 데이터가 없는 테이블: {len(no_data_tables)}개")
            # 처음 20개 표시
            for table_name in no_data_tables[:20]:
                yield emit_message(f"         • {table_name}")
            if len(no_data_tables) > 20:
                yield emit_message(f"         ... 외 {len(no_data_tables) - 20}개")
        
        # 실패한 테이블 목록을 파일로 저장
        await _save_failed_tables_to_file(failed_tables, failure_reasons)
        yield emit_message(f"      💾 실패한 테이블 목록 저장 완료: failed_tables.json (log 폴더)")
        
        # 처음 10개만 상세 표시
        yield emit_message(f"      📋 실패한 테이블 목록 (상위 10개):")
        for table_name, reason in failed_tables[:10]:
            yield emit_message(f"         ✗ {table_name}: {reason}")
        if len(failed_tables) > 10:
            yield emit_message(f"         ... 외 {len(failed_tables) - 10}개")
    
    log_process(
        "METADATA",
        "ENRICH_DONE",
        f"메타데이터 보강 완료: {enriched_count}/{total_tables}개 테이블, "
        f"테이블 설명 {tables_updated}개, 컬럼 설명 {columns_updated}개, 실패: {len(failed_tables)}개",
        logging.INFO,
    )
    yield emit_message(f"   ✅ 메타데이터 보강 완료: 성공 {enriched_count}개, 실패 {len(failed_tables)}개 / 전체 {total_tables}개")


async def _process_table_description(
    pipeline: Any,
    client: Neo4jClient,
    session: aiohttp.ClientSession,
    enrichment_service: MetadataEnrichmentService,
    table_name: str,
    schema_name: str,
    full_table_name: str,
) -> AsyncGenerator[bytes | str | tuple[int, int], None]:
    """단일 테이블 설명 처리"""
    # 샘플 데이터 조회
    sample_sql = f"SELECT * FROM {full_table_name} LIMIT {settings.metadata_enrichment.fk_sample_size}"
    sample_data = await enrichment_service.fetch_sample_data(session, sample_sql)
    
    if not sample_data:
        yield f"샘플 데이터 없음 (SQL: {sample_sql[:100]})"
        yield (0, 0)
        return
    
    # 샘플 데이터 확인
    if len(sample_data) == 0:
        yield f"샘플 데이터 조회 결과 빈 배열"
        yield (0, 0)
        return
    
    # 컬럼 정보 조회
    columns_info = await _get_columns_info(pipeline, client, table_name, schema_name)
    
    if not columns_info:
        yield f"컬럼 정보 조회 실패"
        yield (0, 0)
        return
    
    # LLM으로 설명 생성
    try:
        descriptions = await enrichment_service.generate_descriptions_from_sample(
            table_name, schema_name, sample_data, columns_info
        )
    except Exception as e:
        yield f"LLM 설명 생성 예외: {str(e)[:100]}"
        yield (0, 0)
        return
    
    if not descriptions:
        yield f"LLM 설명 생성 실패 (응답 없음)"
        yield (0, 0)
        return
    
    # Neo4j 업데이트
    try:
        async with pipeline._cypher_lock:
            t_updated, c_updated = await enrichment_service.update_descriptions_in_neo4j(
                table_name, schema_name, descriptions
            )
    except Exception as e:
        yield f"Neo4j 업데이트 실패: {str(e)[:100]}"
        yield (0, 0)
        return
    
    # 스트리밍 데이터 생성
    if t_updated or c_updated:
        async for chunk in _emit_description_updates(schema_name, table_name, descriptions):
            yield chunk
    
    yield (t_updated, c_updated)


async def _get_columns_info(pipeline: Any, client: Neo4jClient, table_name: str, schema_name: str) -> List[Dict[str, Any]]:
    """컬럼 정보 조회"""
    query = """
    MATCH (t:Table {name: $table_name})-[:HAS_COLUMN]->(c:Column)
    WHERE t.schema = $schema_name
    RETURN c.name AS column_name, 
           coalesce(c.dtype, c.dataType) AS data_type, 
           c.description AS description
    ORDER BY c.name
    """
    
    async with pipeline._cypher_lock:
        result = await client.execute_with_params(query, {"table_name": table_name, "schema_name": schema_name})
    
    return result if isinstance(result, list) else []


async def _emit_description_updates(
    schema_name: str, table_name: str, descriptions: Dict[str, Any]
) -> AsyncGenerator[bytes, None]:
    """설명 업데이트 이벤트 및 그래프 데이터 스트리밍"""
    nodes = []
    relationships = []
    table_fqn = f"{schema_name}.{table_name}"
    
    # 테이블 노드 업데이트
    table_desc = descriptions.get("table_description", "")
    if table_desc:
        yield emit_node_event(
            action="updated",
            node_type="Table",
            node_name=table_fqn,
            details={"description": table_desc, "description_source": DESCRIPTION_SOURCE},
        )
        nodes.append({
            "Labels": ["Table"],
            "Properties": {
                "name": table_name,
                "schema": schema_name,
                "description": table_desc,
                "description_source": DESCRIPTION_SOURCE,
            },
        })
    
    # 컬럼 노드 업데이트
    column_descs = descriptions.get("column_descriptions", {})
    for col_name, col_desc in column_descs.items():
        col_fqn = f"{table_fqn}.{col_name}"
        yield emit_node_event(
            action="updated",
            node_type="Column",
            node_name=col_fqn,
            details={"description": col_desc, "description_source": DESCRIPTION_SOURCE},
        )
        nodes.append({
            "Labels": ["Column"],
            "Properties": {"fqn": col_fqn, "name": col_name, "description": col_desc, "description_source": DESCRIPTION_SOURCE},
        })
        relationships.append({
            "Type": "HAS_COLUMN",
            "StartNode": {"Labels": ["Table"], "Properties": {"name": table_name, "schema": schema_name}},
            "EndNode": {"Labels": ["Column"], "Properties": {"fqn": col_fqn}},
        })
    
    # 그래프 데이터 스트리밍
    if nodes or relationships:
        yield emit_data(graph={"Nodes": nodes, "Relationships": relationships}, current_file=table_fqn)


async def _complete_phase(stats: AnalysisStats) -> AsyncGenerator[bytes, None]:
    """Phase 완료 이벤트"""
    yield emit_phase_event(
        phase_num=PHASE_NUM,
        phase_name=PHASE_NAME,
        status="completed",
        progress=100,
        details={
            "tables_enriched": getattr(stats, "tables_enriched", 0),
            "columns_enriched": getattr(stats, "columns_enriched", 0),
        },
    )


async def _run_fk_inference(
    pipeline: Any,
    client: Neo4jClient,
    session: aiohttp.ClientSession,
    enrichment_service: MetadataEnrichmentService,
    stats: AnalysisStats,
    tables_without_desc: List[Dict[str, Any]],
) -> AsyncGenerator[bytes, None]:
    """FK 관계 추론 실행"""
    log_process("FK_INFERENCE", "START", "FK 관계 추론 시작", logging.INFO)
    yield emit_message("   🔗 [Phase 3.5-2] FK 관계 추론 시작...")
    yield emit_phase_event(
        phase_num=PHASE_NUM,
        phase_name="FK 관계 추론",
        status="in_progress",
        progress=0,
        details={"step": "fk_inference"},
    )
    
    # 테이블 목록 조회
    source_tables, all_tables = [], []
    async for chunk in _get_fk_inference_tables(pipeline, client, tables_without_desc):
        if isinstance(chunk, tuple):
            source_tables, all_tables = chunk
        else:
            yield chunk
    
    if not source_tables:
        yield emit_message(f"   ⚠️ 소스 테이블이 비어있습니다 (조회된 테이블: {len(source_tables)}개)")
        yield emit_message("      → FK 추론을 수행할 수 없습니다")
        return
    
    if len(all_tables) < 2:
        yield emit_message(f"   ⚠️ 전체 테이블이 2개 미만입니다 (조회된 테이블: {len(all_tables)}개)")
        yield emit_message("      → FK 추론을 수행할 수 없습니다 (최소 2개 테이블 필요)")
        return
    
    yield emit_message(f"   📊 FK 추론 시작: 소스 {len(source_tables)}개 × 타겟 {len(all_tables)}개 = 최대 {len(source_tables) * len(all_tables)}개 비교")
    
    # FK 후보 추출 (유사도 기반 + PK 값 기반 fallback)
    yield emit_message("   🔍 컬럼명 유사도 기반 후보 쌍 추출 중...")
    candidates = await enrichment_service.find_fk_candidates(source_tables, all_tables, session)
    if not candidates:
        yield emit_message("   ℹ️ FK 후보 쌍이 발견되지 않았습니다")
        return
    
    yield emit_message(f"   📊 후보 쌍 발견: {len(candidates)}개")
    
    # 후보 선정 결과를 파일로 저장
    await _save_fk_candidates_to_file(candidates)
    yield emit_message(f"   💾 후보 선정 결과 저장 완료: fk_candidates.json")
    
    # 검증 및 저장
    verified_count = 0
    async for chunk in _verify_and_save_fk_relationships(
        pipeline, client, session, enrichment_service, candidates
    ):
        if isinstance(chunk, int):
            verified_count = chunk
        else:
            yield chunk
    
    # 통계 업데이트
    stats.fk_relationships_inferred = getattr(stats, "fk_relationships_inferred", 0) + verified_count
    
    log_process("FK_INFERENCE", "COMPLETE", f"FK 관계 추론 완료: {verified_count}/{len(candidates)}개 확정", logging.INFO)
    yield emit_message(f"   ✅ FK 관계 추론 완료: {verified_count}개 확정 / 전체 {len(candidates)}개 후보")


async def _save_fk_candidates_to_file(candidates: List[Dict[str, Any]]) -> None:
    """FK 후보 선정 결과를 JSON 파일로 저장 (상세 통계 포함) - logs 폴더에 저장"""
    try:
        # 프로젝트 루트 디렉토리 찾기 (현재 파일 기준으로 3단계 상위)
        current_file = Path(__file__)
        project_root = current_file.parent.parent.parent.parent  # analyzer/strategy/dbms -> 프로젝트 루트
        
        # log 폴더 생성 (없으면)
        log_dir = project_root / "logs"
        log_dir.mkdir(exist_ok=True)
        
        # 파일명: fk_candidates_YYYYMMDD_HHMMSS.json
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filepath = log_dir / f"fk_candidates_{timestamp}.json"
        
        # 소스 컬럼별로 그룹화 (역방향 중복은 이미 후보 생성 단계에서 처리됨)
        candidates_by_source: Dict[str, Dict[str, List[Dict[str, Any]]]] = {}
        
        for c in candidates:
            source_table_key = f"{c.get('from_schema')}.{c.get('from_table')}"
            source_column = c.get('from_column', '')
            
            if source_table_key not in candidates_by_source:
                candidates_by_source[source_table_key] = {}
            if source_column not in candidates_by_source[source_table_key]:
                candidates_by_source[source_table_key][source_column] = []
            
            candidates_by_source[source_table_key][source_column].append({
                "table": f"{c.get('to_schema')}.{c.get('to_table')}",
                "column": c.get('to_column', ''),
                "similarity": c.get("similarity", 0.0),
                "from_is_pk": c.get("from_is_pk", False),
                "to_is_pk": c.get("to_is_pk", False),
            })
        
        # 간소화된 후보 데이터 구조
        candidates_data = []
        for source_table, columns in sorted(candidates_by_source.items()):
            for source_column, targets in sorted(columns.items()):
                # 타겟을 유사도 순으로 정렬 (높은 순)
                targets_sorted = sorted(targets, key=lambda x: (-x["similarity"], x["table"], x["column"]))
                candidates_data.append({
                    "source_table": source_table,
                    "source_column": source_column,
                    "targets": targets_sorted,
                })
        
        # 통계 정보
        stats = {
            "total_candidates": len(candidates),
            "unique_source_tables": len(candidates_by_source),
            "unique_source_columns": sum(len(cols) for cols in candidates_by_source.values()),
            "total_unique_pairs_after_dedup": len(candidates),  # 이미 후보 생성 단계에서 역방향 중복 제거됨
            "generated_at": datetime.now().isoformat(),
        }
        
        # JSON 저장
        output = {
            "statistics": stats,
            "candidates": candidates_data,
        }
        
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(output, f, ensure_ascii=False, indent=2)
        
        log_process(
            "FK_INFERENCE",
            "CANDIDATES_SAVED",
            f"후보 선정 결과 저장: {filepath.name} (log 폴더, {len(candidates)}개 후보 → {len(candidates_data)}개 소스 컬럼)",
            logging.INFO,
        )
        
    except Exception as e:
        log_process(
            "FK_INFERENCE",
            "CANDIDATES_SAVE_ERROR",
            f"후보 선정 결과 저장 실패: {e}",
            logging.ERROR,
            e,
        )


async def _save_failed_tables_to_file(
    failed_tables: List[Tuple[str, str]], 
    failure_reasons: Dict[str, int]
) -> None:
    """실패한 테이블 목록을 JSON 파일로 저장 (log 폴더에 저장)"""
    try:
        # 프로젝트 루트 디렉토리 찾기
        current_file = Path(__file__)
        project_root = current_file.parent.parent.parent.parent  # analyzer/strategy/dbms -> 프로젝트 루트
        
        # log 폴더 생성 (없으면)
        log_dir = project_root / "log"
        log_dir.mkdir(exist_ok=True)
        
        # 파일명: failed_tables_YYYYMMDD_HHMMSS.json
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filepath = log_dir / f"failed_tables_{timestamp}.json"
        
        # 실패 사유별로 그룹화
        tables_by_reason: Dict[str, List[str]] = {}
        for table_name, reason in failed_tables:
            # 사유 정규화
            if "샘플 데이터 없음" in reason:
                normalized_reason = "샘플 데이터 없음"
            elif "LLM" in reason or "응답 없음" in reason:
                normalized_reason = "LLM 설명 생성 실패"
            elif "컬럼 정보" in reason:
                normalized_reason = "컬럼 정보 조회 실패"
            elif "Neo4j" in reason:
                normalized_reason = "Neo4j 업데이트 실패"
            elif "예외 발생" in reason:
                normalized_reason = "예외 발생"
            else:
                normalized_reason = "기타"
            
            if normalized_reason not in tables_by_reason:
                tables_by_reason[normalized_reason] = []
            tables_by_reason[normalized_reason].append(table_name)
        
        # 데이터가 없는 테이블 목록 추출
        no_data_tables = tables_by_reason.get("샘플 데이터 없음", [])
        
        # JSON 구조
        output = {
            "statistics": {
                "total_failed": len(failed_tables),
                "failure_reasons": failure_reasons,
                "no_data_tables_count": len(no_data_tables),
            },
            "tables_by_reason": {
                reason: sorted(tables) 
                for reason, tables in tables_by_reason.items()
            },
            "no_data_tables": sorted(no_data_tables),  # 데이터가 없는 테이블 목록 (쉽게 추출 가능)
            "all_failed_tables": [
                {"table": table_name, "reason": reason}
                for table_name, reason in failed_tables
            ],
            "generated_at": datetime.now().isoformat(),
        }
        
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(output, f, ensure_ascii=False, indent=2)
        
        log_process(
            "METADATA",
            "FAILED_TABLES_SAVED",
            f"실패한 테이블 목록 저장: {filepath.name} (log 폴더, 전체 {len(failed_tables)}개, 데이터 없음 {len(no_data_tables)}개)",
            logging.INFO,
        )
        
    except Exception as e:
        log_process(
            "METADATA",
            "FAILED_TABLES_SAVE_ERROR",
            f"실패한 테이블 목록 저장 실패: {e}",
            logging.ERROR,
            e,
        )


async def _get_fk_inference_tables(
    pipeline: Any, client: Neo4jClient, tables_without_desc: List[Dict[str, Any]]
) -> AsyncGenerator[bytes | tuple[List[Dict[str, Any]], List[Dict[str, Any]]], None]:
    """FK 추론용 테이블 목록 조회
    
    Args:
        tables_without_desc: description이 없는 테이블 목록 (소스 테이블로 사용)
    """
    # 소스 테이블: tables_without_desc를 사용하되, 컬럼 정보를 포함하도록 조회
    if not tables_without_desc:
        yield emit_message("   ⚠️ description이 없는 테이블이 없어서 FK 추론을 수행할 수 없습니다")
        yield emit_message("      → 모든 테이블에 description이 존재하므로 FK 추론 단계를 건너뜁니다")
        yield ([], [])
        return
    
    yield emit_message(f"   📊 FK 추론용 테이블 조회 중... (소스 후보: {len(tables_without_desc)}개)")
    
    # 소스 테이블의 (schema, name) 조합 목록 생성 (파라미터화된 쿼리 사용)
    source_table_pairs = [
        {"schema": t.get("schema_name", "public"), "name": t.get("table_name", "")}
        for t in tables_without_desc
        if t.get("table_name")
    ]
    
    if not source_table_pairs:
        yield emit_message("   ⚠️ 유효한 소스 테이블이 없습니다 (table_name이 비어있음)")
        yield ([], [])
        return
    
    yield emit_message(f"      → 유효한 소스 테이블: {len(source_table_pairs)}개")
    
    source_query = """
    UNWIND $table_pairs AS pair
    MATCH (t:Table)-[:HAS_COLUMN]->(c:Column)
    WHERE t.schema = pair.schema AND t.name = pair.name
    RETURN t.name AS table_name,
           t.schema AS schema_name,
           collect({
               column_name: c.name,
               data_type: coalesce(c.dtype, c.dataType),
               nullable: c.nullable,
               is_primary_key: coalesce(c.is_primary_key, false)
           }) AS columns
    ORDER BY t.schema, t.name
    """
    
    # 전체 테이블 조회 (타겟 테이블)
    all_tables_query = """
    MATCH (t:Table)-[:HAS_COLUMN]->(c:Column)
    RETURN t.name AS table_name,
           t.schema AS schema_name,
           collect({
               column_name: c.name,
               data_type: coalesce(c.dtype, c.dataType),
               nullable: c.nullable,
               is_primary_key: coalesce(c.is_primary_key, false)
           }) AS columns
    ORDER BY t.schema, t.name
    """
    
    try:
        async with pipeline._cypher_lock:
            source_results = await client.execute_with_params(
                source_query,
                {"table_pairs": source_table_pairs}
            )
            all_results = await client.execute_queries([all_tables_query])
        
        source_tables = source_results if isinstance(source_results, list) else []
        all_tables = all_results[0] if all_results and len(all_results) > 0 else []
        
        if len(source_tables) == 0:
            yield emit_message("   ⚠️ 소스 테이블의 컬럼 정보를 조회할 수 없습니다")
            yield emit_message(f"      → 조회 요청: {len(source_table_pairs)}개, 조회 결과: {len(source_tables)}개")
            yield emit_message("      → Neo4j에 해당 테이블이 존재하지 않거나 컬럼 정보가 없을 수 있습니다")
            yield ([], [])
            return
        
        if len(all_tables) < 2:
            yield emit_message(f"   ⚠️ 테이블이 2개 미만이어서 FK 추론을 수행할 수 없습니다 (전체: {len(all_tables)}개)")
            yield ([], [])
            return
        
        # 소스 테이블 예시 표시
        source_samples = [f"{t.get('schema_name', 'public')}.{t.get('table_name', '')}" for t in source_tables[:3]]
        source_sample_str = ", ".join(source_samples)
        if len(source_tables) > 3:
            source_sample_str += f" 외 {len(source_tables) - 3}개"
        
        log_process(
            "FK_INFERENCE",
            "TABLES",
            f"소스 테이블: {len(source_tables)}개, 타겟 테이블: {len(all_tables)}개",
            logging.INFO,
        )
        yield emit_message(f"   ✅ 테이블 조회 완료")
        yield emit_message(f"      → 소스 테이블: {len(source_tables)}개 (예: {source_sample_str})")
        yield emit_message(f"      → 타겟 테이블: {len(all_tables)}개 (전체 테이블)")
        
        yield (source_tables, all_tables)
        
    except Exception as e:
        log_process("FK_INFERENCE", "ERROR", f"테이블 조회 실패: {e}", logging.ERROR, e)
        yield emit_message(f"   ⚠️ 테이블 조회 실패: {str(e)[:100]}")
        yield ([], [])


def _normalize_failure_reason(reason: str) -> str:
    """실패 사유를 정규화하여 그룹화
    
    예: "매칭 비율 부족: 13/25 (52% < 80%)" → "매칭 비율 부족"
        "매칭 데이터 없음 (0건)" → "매칭 데이터 없음"
        "매칭 데이터 없음 (빈 결과)" → "매칭 데이터 없음"
    """
    if not reason:
        return "알 수 없는 이유"
    
    reason_lower = reason.lower()
    
    # 매칭 비율 부족 (다양한 형식 통일)
    if "매칭 비율 부족" in reason or "match_ratio" in reason_lower or "비율 부족" in reason:
        return "매칭 비율 부족"
    
    # 매칭 데이터 없음 (다양한 형식 통일)
    if "매칭 데이터 없음" in reason or "matched_count" in reason_lower or "0건" in reason or "빈 결과" in reason:
        return "매칭 데이터 없음"
    
    # 소스 데이터 없음
    if "소스" in reason and ("데이터 없음" in reason or "sample" in reason_lower):
        return "소스 데이터 없음"
    
    # SQL 실행 오류
    if "sql" in reason_lower and ("오류" in reason or "실패" in reason or "error" in reason_lower):
        return "SQL 실행 오류"
    
    # 타겟 테이블/컬럼 불일치
    if "테이블" in reason and ("불일치" in reason or "실패" in reason):
        return "타겟 테이블/컬럼 불일치"
    
    # 샘플 값 이스케이프 실패
    if "이스케이프" in reason or "escape" in reason_lower:
        return "샘플 값 이스케이프 실패"
    
    # 예외 발생
    if "예외" in reason or "exception" in reason_lower:
        return "예외 발생"
    
    # 그 외는 원본 반환 (하지만 길이 제한)
    return reason[:100] if len(reason) > 100 else reason


async def _verify_and_save_fk_relationships(
    pipeline: Any,
    client: Neo4jClient,
    session: aiohttp.ClientSession,
    enrichment_service: MetadataEnrichmentService,
    candidates: List[Dict[str, Any]],
) -> AsyncGenerator[bytes | int, None]:
    """FK 관계 검증 및 저장
    
    전략: 유사도로 뽑힌 후보 → 해당 타겟 테이블에만 샘플데이터 IN 검증
    """
    total_candidates = len(candidates)
    yield emit_message(f"   ✅ 샘플 데이터 매칭 검증 중... (후보: {total_candidates}개)")
    
    verified_count = 0
    # 실패 사유별 집계 (정규화된 사유로 그룹화)
    fail_stats: Dict[str, int] = {}
    # 상세 사유 저장 (디버깅용, 최대 5개만)
    detailed_reasons: Dict[str, List[str]] = {}
    
    try:
        # 전체 후보 검증 (내부에서 소스 컬럼별 그룹화 + 개별 타겟 SQL 실행)
        all_results = await enrichment_service.verify_fk_relationships_batch(session, candidates)
        
        # 결과 처리
        for candidate in candidates:
            key = enrichment_service._get_candidate_key(candidate)
            result = all_results.get(key)
            
            if not result:
                normalized_reason = "결과 없음"
                fail_stats[normalized_reason] = fail_stats.get(normalized_reason, 0) + 1
                continue
            
            # 실패한 경우
            if isinstance(result, dict) and result.get("success") is False:
                original_reason = result.get("reason", "알 수 없는 이유")
                normalized_reason = _normalize_failure_reason(original_reason)
                fail_stats[normalized_reason] = fail_stats.get(normalized_reason, 0) + 1
                
                # 상세 사유 저장 (최대 5개)
                if normalized_reason not in detailed_reasons:
                    detailed_reasons[normalized_reason] = []
                if len(detailed_reasons[normalized_reason]) < 5:
                    detailed_reasons[normalized_reason].append(original_reason)
                continue
            
            # 성공한 경우 → 저장
            if result.get("success") is not False:
                if await _check_and_save_fk_relationship(pipeline, client, enrichment_service, result):
                    verified_count += 1
                    from_key = f"{result.get('from_schema')}.{result.get('from_table')}.{result.get('from_column')}"
                    to_key = f"{result.get('to_schema')}.{result.get('to_table')}.{result.get('to_column')}"
                    yield emit_message(f"      ✓ FK 확정: {from_key} → {to_key} (매칭: {result.get('match_ratio', 0):.0%})")
                    async for chunk in _emit_fk_graph_data(result):
                        yield chunk
        
    except Exception as e:
        log_process("FK_INFERENCE", "VERIFY_ERROR", f"검증 실패: {e}", logging.ERROR, e)
        yield emit_message(f"      ⚠️ 검증 처리 실패: {str(e)[:100]}")
    
    # 실패 사유별 요약 출력 (정규화된 사유로 그룹화)
    total_failed = sum(fail_stats.values())
    if fail_stats:
        yield emit_message(f"   📊 FK 검증 실패 요약: {total_failed}건")
        for reason, count in sorted(fail_stats.items(), key=lambda x: -x[1]):
            # 상세 사유가 있으면 예시 표시
            detail_examples = detailed_reasons.get(reason, [])
            if detail_examples and len(detail_examples) > 0:
                example = detail_examples[0]
                if len(example) > 60:
                    example = example[:57] + "..."
                yield emit_message(f"      • {reason}: {count}건 (예: {example})")
            else:
                yield emit_message(f"      • {reason}: {count}건")
    
    yield verified_count


async def _check_and_save_fk_relationship(
    pipeline: Any,
    client: Neo4jClient,
    enrichment_service: MetadataEnrichmentService,
    result: Dict[str, Any],
) -> bool:
    """역방향 FK 체크 및 저장 (기본키 우선순위 고려)"""
    # 역방향 FK 관계와 기본키 정보 조회
    reverse_check_query = """
    MATCH (t2:Table {name: $from_table, schema: $from_schema})-[r:FK_TO_TABLE]->(t1:Table {name: $to_table, schema: $to_schema})
    WHERE r.sourceColumn = $to_column AND r.targetColumn = $from_column
    MATCH (t2)-[:HAS_COLUMN]->(c2:Column {name: $from_column})
    MATCH (t1)-[:HAS_COLUMN]->(c1:Column {name: $to_column})
    RETURN r, c2.is_primary_key AS existing_from_is_pk, c1.is_primary_key AS existing_to_is_pk
    """
    
    async with pipeline._cypher_lock:
        reverse_result = await client.execute_with_params(
            reverse_check_query,
            {
                "from_table": result["from_table"],
                "from_schema": result["from_schema"],
                "to_table": result["to_table"],
                "to_schema": result["to_schema"],
                "from_column": result["from_column"],
                "to_column": result["to_column"],
            },
        )
        
        if reverse_result and len(reverse_result) > 0:
            # 역방향 관계가 있는 경우, 기본키 우선순위 확인
            existing_from_is_pk = reverse_result[0].get("existing_from_is_pk", False)
            existing_to_is_pk = reverse_result[0].get("existing_to_is_pk", False)
            current_from_is_pk = result.get("from_is_pk", False)
            current_to_is_pk = result.get("to_is_pk", False)
            
            # 기본키 우선순위 규칙:
            # 1. 타겟이 기본키인 쪽을 우선 선택
            # 2. 양쪽 모두 기본키가 없으면, 한쪽이 이미 있으면 연결하지 않음
            if current_to_is_pk and not existing_to_is_pk:
                # 현재 후보가 더 우선순위가 높음 (타겟이 PK) - 기존 것을 삭제하고 새로 저장
                delete_query = """
                MATCH (t2:Table {name: $from_table, schema: $from_schema})-[r:FK_TO_TABLE]->(t1:Table {name: $to_table, schema: $to_schema})
                WHERE r.sourceColumn = $to_column AND r.targetColumn = $from_column
                DELETE r
                """
                await client.execute_with_params(delete_query, {
                    "from_table": result["from_table"],
                    "from_schema": result["from_schema"],
                    "to_table": result["to_table"],
                    "to_schema": result["to_schema"],
                    "from_column": result["from_column"],
                    "to_column": result["to_column"],
                })
                # 새로 저장
                await enrichment_service.save_fk_relationship(result)
                return True
            elif not current_to_is_pk and not existing_to_is_pk:
                # 양쪽 모두 기본키가 없으면, 한쪽이 이미 있으면 연결하지 않음
                return False
            else:
                # 기존 것이 더 우선순위가 높거나 같으면 현재 것은 스킵
                return False
        
        # 역방향 관계가 없으면 저장
        await enrichment_service.save_fk_relationship(result)
        return True


async def _emit_fk_error(candidate: Dict[str, Any], error_msg: str) -> AsyncGenerator[bytes, None]:
    """FK 검증 예외 메시지"""
    log_process(
        "FK_INFERENCE",
        "VERIFY_ERROR",
        f"FK 검증 예외: {candidate.get('from_table')}.{candidate.get('from_column')} → "
        f"{candidate.get('to_table')}.{candidate.get('to_column')} - {error_msg}",
        logging.WARNING,
    )
    yield emit_message(
        f"         ⚠️ FK 검증 예외: "
        f"{candidate.get('from_schema')}.{candidate.get('from_table')}.{candidate.get('from_column')} → "
        f"{candidate.get('to_schema')}.{candidate.get('to_table')}.{candidate.get('to_column')} "
        f"- {error_msg[:100]}"
    )


async def _emit_fk_graph_data(result: Dict[str, Any]) -> AsyncGenerator[bytes, None]:
    """FK 관계 그래프 데이터 스트리밍"""
    from_table_fqn = f"{result['from_schema']}.{result['from_table']}"
    to_table_fqn = f"{result['to_schema']}.{result['to_table']}"
    from_col_fqn = f"{from_table_fqn}.{result['from_column']}"
    to_col_fqn = f"{to_table_fqn}.{result['to_column']}"
    
    # 관계 이벤트
    yield emit_relationship_event(
        action="created",
        rel_type="FK_TO_TABLE",
        source=from_table_fqn,
        target=to_table_fqn,
        details={
            "sourceColumn": result["from_column"],
            "targetColumn": result["to_column"],
            "similarity": result.get("similarity", 0.0),
            "match_ratio": result.get("match_ratio", 0.0),
            "source": DESCRIPTION_SOURCE,
        },
    )
    
    yield emit_relationship_event(
        action="created",
        rel_type="FK_TO_COLUMN",
        source=from_col_fqn,
        target=to_col_fqn,
        details={
            "similarity": result.get("similarity", 0.0),
            "match_ratio": result.get("match_ratio", 0.0),
            "source": DESCRIPTION_SOURCE,
        },
    )
    
    # 그래프 데이터
    relationships = [
        {
            "Type": "FK_TO_TABLE",
            "StartNode": {"Labels": ["Table"], "Properties": {"name": result["from_table"], "schema": result["from_schema"]}},
            "EndNode": {"Labels": ["Table"], "Properties": {"name": result["to_table"], "schema": result["to_schema"]}},
            "Properties": {
                "sourceColumn": result["from_column"],
                "targetColumn": result["to_column"],
                "similarity": result.get("similarity", 0.0),
                "match_ratio": result.get("match_ratio", 0.0),
                "source": DESCRIPTION_SOURCE,
            },
        },
        {
            "Type": "FK_TO_COLUMN",
            "StartNode": {"Labels": ["Column"], "Properties": {"fqn": from_col_fqn}},
            "EndNode": {"Labels": ["Column"], "Properties": {"fqn": to_col_fqn}},
            "Properties": {
                "similarity": result.get("similarity", 0.0),
                "match_ratio": result.get("match_ratio", 0.0),
                "source": DESCRIPTION_SOURCE,
            },
        },
    ]
    
    yield emit_data(graph={"Nodes": [], "Relationships": relationships}, current_file=f"FK: {from_table_fqn} → {to_table_fqn}")
