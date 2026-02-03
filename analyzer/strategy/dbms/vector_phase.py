"""벡터라이징 Phase (Phase 4) - DBMS

dbms_analyzer.py에서 분리된 Phase 4 로직입니다.
모든 로직은 100% 보존되며, 위치만 변경되었습니다.
"""

import asyncio
import logging
import time
from typing import Any, AsyncGenerator

from client.neo4j_client import Neo4jClient
from analyzer.strategy.base_analyzer import AnalysisStats
from client.embedding_client import EmbeddingClient
from config.settings import settings
from util.stream_event import (
    emit_message,
    emit_phase_event,
)
from util.text_utils import log_process


async def run_vectorize_phase(
    analyzer: Any,
    client: Neo4jClient,
    orchestrator: Any,
    stats: AnalysisStats,
) -> AsyncGenerator[bytes, None]:
    """Phase 4: 테이블/컬럼 벡터라이징 (배치 최적화)
    
    Neo4j에 저장된 테이블/컬럼의 description을 기반으로 임베딩 생성
    배치 처리로 성능 최적화
    
    Args:
        analyzer: DbmsAnalyzer 인스턴스 (공유 상태 접근용)
        client: Neo4j 클라이언트
        orchestrator: 오케스트레이터
        stats: 분석 통계
    """
    from openai import AsyncOpenAI
    
    # OpenAI 클라이언트 초기화
    api_key = orchestrator.api_key or settings.openai_api_key
    if not api_key:
        yield emit_message("   ⚠️ OpenAI API 키가 없어 벡터라이징을 건너뜁니다")
        return
    
    openai_client = AsyncOpenAI(api_key=api_key)
    embedding_client = EmbeddingClient(openai_client)
    
    # ===========================================
    # 테이블 벡터라이징 (배치 처리)
    # ===========================================
    yield emit_message("   📊 [Phase 4-1] 테이블 벡터라이징 시작...")
    yield emit_phase_event(
        phase_num=4,
        phase_name="벡터라이징",
        status="in_progress",
        progress=0,
        details={"step": "table_vectorizing"}
    )
    
    # description과 analyzed_description을 합쳐서 임베딩 생성 (검색 품질 향상)
    table_query = """
    MATCH (__cy_t__:Table)
    WHERE (__cy_t__.vector IS NULL OR size(__cy_t__.vector) = 0)
      AND (__cy_t__.description IS NOT NULL OR __cy_t__.analyzed_description IS NOT NULL)
    RETURN elementId(__cy_t__) AS tid, 
           __cy_t__.name AS name,
           __cy_t__.schema AS schema,
           trim(
             coalesce(__cy_t__.description, '') + 
             CASE WHEN __cy_t__.analyzed_description IS NOT NULL AND __cy_t__.analyzed_description <> '' 
                  THEN ' | AI 분석: ' + __cy_t__.analyzed_description 
                  ELSE '' 
             END
           ) AS description
    ORDER BY __cy_t__.schema, __cy_t__.name
    """
    
    try:
        async with analyzer._cypher_lock:
            result = await client.execute_queries([table_query])
        
        tables = result[0] if result and result[0] else []
        total_tables = len(tables)
        
        if total_tables == 0:
            yield emit_message("      ℹ️ 벡터화할 테이블이 없습니다")
        else:
            yield emit_message(f"      📋 벡터화 대상: {total_tables}개 테이블")
            
            # 테이블도 배치로 처리 (50개씩)
            batch_size = 50
            for batch_idx in range(0, total_tables, batch_size):
                batch = tables[batch_idx:batch_idx + batch_size]
                batch_num = batch_idx // batch_size + 1
                total_batches = (total_tables + batch_size - 1) // batch_size
                
                # 유효한 테이블만 필터링
                valid_items = []
                texts = []
                for item in batch:
                    description = item.get("description", "") or ""
                    if not description:
                        continue
                    text = embedding_client.format_table_text(
                        table_name=item.get("name", ""),
                        description=description
                    )
                    texts.append(text)
                    valid_items.append(item)
                
                if not texts:
                    continue
                
                # 배치 진행 상황 표시
                batch_progress = int(batch_idx / total_tables * 25)  # 0-25% 범위
                log_process("VECTORIZE", "TABLE", f"배치 #{batch_num}/{total_batches} 테이블 {len(valid_items)}개 임베딩 생성 시작", logging.INFO)
                yield emit_message(f"      🔄 [{batch_num}/{total_batches}] 테이블 {len(valid_items)}개 임베딩 생성 중...")
                yield emit_phase_event(
                    phase_num=4,
                    phase_name="벡터라이징",
                    status="in_progress",
                    progress=batch_progress,
                    details={"step": "table_embedding", "batch": batch_num, "total_batches": total_batches}
                )
                
                # 배치 임베딩 API 호출 (시간 측정)
                embed_start = time.time()
                vectors = await embedding_client.embed_batch(texts)
                embed_time = time.time() - embed_start
                log_process("VECTORIZE", "API", f"임베딩 API 응답: {len(vectors)}개, {embed_time:.2f}초", logging.INFO)
                
                # UNWIND 배치 저장용 데이터 생성
                vector_updates = []
                for item, vector in zip(valid_items, vectors):
                    if vector:
                        vector_updates.append({
                            "tid": item['tid'],
                            "vector": vector
                        })
                        stats.tables_vectorized += 1
                
                # UNWIND로 한번에 저장
                if vector_updates:
                    update_query = """
                    UNWIND $items AS item
                    MATCH (__cy_t__) WHERE elementId(__cy_t__) = item.tid
                    SET __cy_t__.vector = item.vector
                    RETURN __cy_t__
                    """
                    async with analyzer._cypher_lock:
                        await client.execute_with_params(update_query, {"items": vector_updates})
                    
                    yield emit_message(f"      ✓ [{batch_num}/{total_batches}] {len(vector_updates)}개 테이블 벡터 저장 완료")
            
            yield emit_message(f"   ✅ 테이블 벡터라이징 완료: {stats.tables_vectorized}개 테이블")
        
    except Exception as e:
        error_msg = f"테이블 벡터라이징 실패: {str(e)}"
        yield emit_message(f"   ❌ {error_msg}")
        raise RuntimeError(error_msg) from e
    
    # ===========================================
    # 컬럼 벡터라이징 (배치 처리)
    # ===========================================
    yield emit_message("   📊 [Phase 4-2] 컬럼 벡터라이징 시작...")
    yield emit_phase_event(
        phase_num=4,
        phase_name="벡터라이징",
        status="in_progress",
        progress=25,
        details={"step": "column_vectorizing"}
    )
    
    # description과 analyzed_description을 합쳐서 임베딩 생성 (검색 품질 향상)
    column_query = """
    MATCH (__cy_t__:Table)-[:HAS_COLUMN]->(__cy_c__:Column)
    WHERE (__cy_c__.vector IS NULL OR size(__cy_c__.vector) = 0)
      AND (__cy_c__.description IS NOT NULL OR __cy_c__.analyzed_description IS NOT NULL)
    RETURN elementId(__cy_c__) AS cid,
           __cy_c__.name AS column_name,
           __cy_t__.name AS table_name,
           coalesce(__cy_c__.dtype, '') AS dtype,
           trim(
             coalesce(__cy_c__.description, '') + 
             CASE WHEN __cy_c__.analyzed_description IS NOT NULL AND __cy_c__.analyzed_description <> '' 
                  THEN ' | AI 분석: ' + __cy_c__.analyzed_description 
                  ELSE '' 
             END
           ) AS description
    ORDER BY __cy_t__.schema, __cy_t__.name, __cy_c__.name
    """
    
    try:
        async with analyzer._cypher_lock:
            result = await client.execute_queries([column_query])
        
        columns = result[0] if result and result[0] else []
        total_columns = len(columns)
        
        if total_columns == 0:
            yield emit_message("      ℹ️ 벡터화할 컬럼이 없습니다")
        else:
            yield emit_message(f"      📋 벡터화 대상: {total_columns}개 컬럼")
        
            # 배치 처리 (50개씩)
            batch_size = 50
            for i in range(0, total_columns, batch_size):
                batch = columns[i:i + batch_size]
                batch_num = i // batch_size + 1
                total_batches = (total_columns + batch_size - 1) // batch_size
                texts = []
                
                for item in batch:
                    text = embedding_client.format_column_text(
                        column_name=item.get("column_name", ""),
                        table_name=item.get("table_name", ""),
                        dtype=item.get("dtype", ""),
                        description=item.get("description", "")
                    )
                    texts.append(text)
                
                # 배치 진행 상황 표시
                batch_progress = 25 + int(i / total_columns * 75)  # 25-100% 범위
                log_process("VECTORIZE", "COLUMN", f"배치 #{batch_num}/{total_batches} 컬럼 {len(texts)}개 임베딩 생성 시작", logging.INFO)
                yield emit_message(f"      🔄 [{batch_num}/{total_batches}] 컬럼 {len(texts)}개 임베딩 생성 중...")
                yield emit_phase_event(
                    phase_num=4,
                    phase_name="벡터라이징",
                    status="in_progress",
                    progress=batch_progress,
                    details={"step": "column_embedding", "batch": batch_num, "total_batches": total_batches, "done": i, "total": total_columns}
                )
                
                # 배치 임베딩 API 호출 (시간 측정)
                embed_start = time.time()
                vectors = await embedding_client.embed_batch(texts)
                embed_time = time.time() - embed_start
                log_process("VECTORIZE", "API", f"임베딩 API 응답: {len(vectors)}개, {embed_time:.2f}초", logging.INFO)
                
                # UNWIND 배치 저장용 데이터 생성
                vector_updates = []
                for item, vector in zip(batch, vectors):
                    if vector:
                        vector_updates.append({
                            "cid": item['cid'],
                            "vector": vector
                        })
                        stats.columns_vectorized += 1
                
                # UNWIND로 한번에 저장
                if vector_updates:
                    update_query = """
                    UNWIND $items AS item
                    MATCH (__cy_c__) WHERE elementId(__cy_c__) = item.cid
                    SET __cy_c__.vector = item.vector
                    RETURN __cy_c__
                    """
                    async with analyzer._cypher_lock:
                        await client.execute_with_params(update_query, {"items": vector_updates})
                    
                    yield emit_message(f"      ✓ [{batch_num}/{total_batches}] {len(vector_updates)}개 컬럼 벡터 저장 완료")
            
            yield emit_message(f"   ✅ 컬럼 벡터라이징 완료: {stats.columns_vectorized}개 컬럼")
            yield emit_phase_event(
                phase_num=4,
                phase_name="벡터라이징",
                status="completed",
                progress=100,
                details={"tables_vectorized": stats.tables_vectorized, "columns_vectorized": stats.columns_vectorized}
            )
        
    except Exception as e:
        error_msg = f"컬럼 벡터라이징 실패: {str(e)}"
        yield emit_message(f"   ❌ {error_msg}")
        raise RuntimeError(error_msg) from e

