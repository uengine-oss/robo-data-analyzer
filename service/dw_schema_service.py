"""DW 스타스키마 서비스

OLAP 스타스키마 등록 및 삭제 기능을 제공합니다.

주요 기능:
- DW 스타스키마 등록 (팩트/디멘전 테이블 + 벡터화)
- DW 스타스키마 삭제
"""

import logging
from typing import List, Optional

from fastapi import HTTPException

from analyzer.neo4j_client import Neo4jClient
from client.embedding_client import EmbeddingClient
from config.settings import settings
from util.text_utils import escape_for_cypher


logger = logging.getLogger(__name__)


async def register_star_schema(
    cube_name: str,
    db_name: str,
    dw_schema: str,
    fact_table: dict,
    dimensions: List[dict],
    create_embeddings: bool = True,
    api_key: str = None
) -> dict:
    """DW 스타스키마 등록
    
    Args:
        cube_name: 큐브 이름
        db_name: 데이터베이스 이름
        dw_schema: DW 스키마 이름
        fact_table: 팩트 테이블 정보
        dimensions: 디멘전 테이블 정보 리스트
        create_embeddings: 임베딩 자동 생성 여부
        api_key: OpenAI API 키 (create_embeddings가 True일 때 필수)
        
    Returns:
        등록 결과 통계
    """
    if create_embeddings and not api_key:
        raise HTTPException(400, {"error": "임베딩 생성을 위해 OpenAI API 키가 필요합니다."})
    
    client = Neo4jClient()
    stats = {
        "tables_created": 0,
        "columns_created": 0,
        "relationships_created": 0,
        "embeddings_created": 0
    }
    
    try:
        embedding_client = None
        if create_embeddings and api_key:
            from openai import AsyncOpenAI
            openai_client = AsyncOpenAI(api_key=api_key)
            embedding_client = EmbeddingClient(openai_client)
        
        # 1. 팩트 테이블 생성
        fact_name = fact_table.get("name", "")
        fact_fqn = f"{dw_schema}.{fact_name}"
        
        fact_query = f"""
            MERGE (__cy_t__:Table {{fqn: '{escape_for_cypher(fact_fqn)}'}})
            SET __cy_t__.name = '{escape_for_cypher(fact_name)}',
                __cy_t__.schema = '{escape_for_cypher(dw_schema)}',
                __cy_t__.db_name = '{escape_for_cypher(db_name)}',
                __cy_t__.table_type = 'FACT',
                __cy_t__.cube_name = '{escape_for_cypher(cube_name)}'
            RETURN __cy_t__.name AS name
        """
        
        await client.execute_queries([fact_query])
        stats["tables_created"] += 1
        
        # 팩트 테이블 컬럼 생성
        for col in fact_table.get("columns", []):
            col_fqn = f"{fact_fqn}.{col['name']}"
            col_query = f"""
                MATCH (__cy_t__:Table {{fqn: '{escape_for_cypher(fact_fqn)}'}})
                MERGE (__cy_t__)-[:HAS_COLUMN]->(__cy_c__:Column {{fqn: '{escape_for_cypher(col_fqn)}'}})
                SET __cy_c__.name = '{escape_for_cypher(col["name"])}',
                    __cy_c__.dtype = '{escape_for_cypher(col.get("dtype", "VARCHAR"))}',
                    __cy_c__.description = '{escape_for_cypher(col.get("description") or "")}',
                    __cy_c__.is_pk = {str(col.get("is_pk", False)).lower()},
                    __cy_c__.is_fk = {str(col.get("is_fk", False)).lower()}
            """
            await client.execute_queries([col_query])
            stats["columns_created"] += 1
        
        # 2. 디멘전 테이블 생성
        for dim in dimensions:
            dim_name = dim.get("name", "")
            dim_fqn = f"{dw_schema}.{dim_name}"
            
            dim_query = f"""
                MERGE (__cy_t__:Table {{fqn: '{escape_for_cypher(dim_fqn)}'}})
                SET __cy_t__.name = '{escape_for_cypher(dim_name)}',
                    __cy_t__.schema = '{escape_for_cypher(dw_schema)}',
                    __cy_t__.db_name = '{escape_for_cypher(db_name)}',
                    __cy_t__.table_type = 'DIMENSION',
                    __cy_t__.cube_name = '{escape_for_cypher(cube_name)}'
                RETURN __cy_t__.name AS name
            """
            
            await client.execute_queries([dim_query])
            stats["tables_created"] += 1
            
            # 디멘전 테이블 컬럼 생성
            for col in dim.get("columns", []):
                col_fqn = f"{dim_fqn}.{col['name']}"
                col_query = f"""
                    MATCH (__cy_t__:Table {{fqn: '{escape_for_cypher(dim_fqn)}'}})
                    MERGE (__cy_t__)-[:HAS_COLUMN]->(__cy_c__:Column {{fqn: '{escape_for_cypher(col_fqn)}'}})
                    SET __cy_c__.name = '{escape_for_cypher(col["name"])}',
                        __cy_c__.dtype = '{escape_for_cypher(col.get("dtype", "VARCHAR"))}',
                        __cy_c__.description = '{escape_for_cypher(col.get("description") or "")}'
                """
                await client.execute_queries([col_query])
                stats["columns_created"] += 1
        
        # 3. FK 관계 생성 (팩트 → 디멘전)
        for col in fact_table.get("columns", []):
            if col.get("is_fk") and col.get("fk_target_table"):
                target_table = col["fk_target_table"]
                fk_query = f"""
                    MATCH (__cy_f__:Table {{fqn: '{escape_for_cypher(fact_fqn)}'}})
                    MATCH (__cy_d__:Table {{fqn: '{escape_for_cypher(target_table)}'}})
                    MERGE (__cy_f__)-[__cy_r__:FK_TO_TABLE]->(__cy_d__)
                    SET __cy_r__.sourceColumn = '{escape_for_cypher(col["name"])}',
                        __cy_r__.source = 'user'
                """
                await client.execute_queries([fk_query])
                stats["relationships_created"] += 1
        
        # 4. 임베딩 생성
        if embedding_client:
            # 팩트 테이블 임베딩
            try:
                fact_text = EmbeddingClient.format_table_text(
                    fact_name,
                    "",
                    [col["name"] for col in fact_table.get("columns", [])]
                )
                fact_embedding = await embedding_client.embed_text(fact_text)
                
                if fact_embedding:
                    embed_query = f"""
                        MATCH (__cy_t__:Table {{fqn: '{escape_for_cypher(fact_fqn)}'}})
                        SET __cy_t__.embedding = {fact_embedding}
                    """
                    await client.execute_queries([embed_query])
                    stats["embeddings_created"] += 1
            except Exception as e:
                error_msg = f"팩트 테이블 임베딩 실패: {e}"
                logger.error(error_msg)
                raise RuntimeError(error_msg) from e
            
            # 디멘전 테이블 임베딩
            for dim in dimensions:
                try:
                    dim_fqn = f"{dw_schema}.{dim['name']}"
                    dim_text = EmbeddingClient.format_table_text(
                        dim["name"],
                        "",
                        [col["name"] for col in dim.get("columns", [])]
                    )
                    dim_embedding = await embedding_client.embed_text(dim_text)
                    
                    if dim_embedding:
                        embed_query = f"""
                            MATCH (__cy_t__:Table {{fqn: '{escape_for_cypher(dim_fqn)}'}})
                            SET __cy_t__.embedding = {dim_embedding}
                        """
                        await client.execute_queries([embed_query])
                        stats["embeddings_created"] += 1
                except Exception as e:
                    error_msg = f"디멘전 테이블 '{dim['name']}' 임베딩 실패: {e}"
                    logger.error(error_msg)
                    raise RuntimeError(error_msg) from e
        
        return {
            "message": f"스타스키마 '{cube_name}'이 등록되었습니다.",
            "stats": stats
        }
    finally:
        await client.close()


async def delete_star_schema(
    cube_name: str,
    schema: str = "dw",
    db_name: str = "postgres"
) -> dict:
    """DW 스타스키마 삭제
    
    Args:
        cube_name: 큐브 이름
        schema: 스키마 이름
        db_name: 데이터베이스 이름
        
    Returns:
        삭제 결과
    """
    client = Neo4jClient()
    try:
        # 큐브에 속한 모든 테이블과 컬럼, 관계 삭제
        query = f"""
            MATCH (__cy_t__:Table {{cube_name: '{escape_for_cypher(cube_name)}'}})
            OPTIONAL MATCH (__cy_t__)-[:HAS_COLUMN]->(__cy_c__:Column)
            OPTIONAL MATCH (__cy_t__)-[__cy_r__:FK_TO_TABLE]->()
            DETACH DELETE __cy_t__, __cy_c__
            RETURN count(DISTINCT __cy_t__) AS deleted_tables
        """
        
        results = await client.execute_queries([query])
        deleted = results[0][0]["deleted_tables"] if results and results[0] else 0
        
        return {
            "message": f"스타스키마 '{cube_name}'이 삭제되었습니다.",
            "deleted_tables": deleted
        }
    finally:
        await client.close()

