"""스키마 관리 서비스

테이블, 컬럼, 관계 등 스키마 CRUD 기능을 제공합니다.

주요 기능:
- 시멘틱 검색
- 테이블/컬럼 조회
- 관계 조회/생성/삭제
- 설명 업데이트
- 벡터라이징
"""

import logging
from typing import Optional, List

from fastapi import HTTPException

from client.neo4j_client import Neo4jClient
from client.embedding_client import EmbeddingClient
from config.settings import settings


logger = logging.getLogger(__name__)


# =============================================================================
# 시멘틱 검색
# =============================================================================

async def search_tables_by_semantic(
    query: str,
    limit: int,
    api_key: str
) -> list:
    """시멘틱 검색: 테이블 설명의 의미적 유사도 기반 검색
    
    Args:
        query: 검색 쿼리
        limit: 결과 제한
        api_key: OpenAI API 키 (필수)
        
    Returns:
        [{"name": str, "schema": str, "description": str, "similarity": float}, ...]
    """
    if not api_key:
        raise HTTPException(400, {"error": "OpenAI API 키가 필요합니다."})
    
    try:
        import numpy as np
        from openai import AsyncOpenAI
    except ImportError as e:
        raise HTTPException(500, {"error": f"필수 라이브러리가 설치되지 않았습니다: {e}"})
    
    client = Neo4jClient()
    
    try:
        openai_client = AsyncOpenAI(api_key=api_key)
    except Exception as e:
        raise HTTPException(400, {"error": f"OpenAI API 키가 유효하지 않습니다: {e}"})
    
    try:
        cypher_query = """
            MATCH (__cy_t__:Table)
            WHERE __cy_t__.description IS NOT NULL AND __cy_t__.description <> ''
            RETURN __cy_t__.name AS name,
                   __cy_t__.schema AS schema,
                   __cy_t__.description AS description
            ORDER BY __cy_t__.name
            LIMIT 200
        """
        
        results = await client.execute_queries([cypher_query])
        records = results[0] if results else []
        
        if not records:
            return []
        
        # 쿼리 임베딩 생성
        query_response = await openai_client.embeddings.create(
            model="text-embedding-3-small",
            input=query
        )
        query_embedding = np.array(query_response.data[0].embedding)
        
        # 테이블 설명 임베딩 생성
        descriptions = [
            (r.get("description") or "no description")[:500]
            for r in records
        ]
        
        desc_response = await openai_client.embeddings.create(
            model="text-embedding-3-small",
            input=descriptions
        )
        
        # 유사도 계산
        results_with_similarity = []
        for i, record in enumerate(records):
            desc_embedding = np.array(desc_response.data[i].embedding)
            
            norm_product = np.linalg.norm(query_embedding) * np.linalg.norm(desc_embedding)
            if norm_product == 0:
                similarity = 0.0
            else:
                similarity = float(np.dot(query_embedding, desc_embedding) / norm_product)
            
            results_with_similarity.append({
                "name": record["name"],
                "schema": record["schema"] or "public",
                "description": record["description"][:200],
                "similarity": round(similarity, 4)
            })
        
        results_with_similarity.sort(key=lambda x: x["similarity"], reverse=True)
        filtered_results = [r for r in results_with_similarity[:limit] if r["similarity"] >= 0.3]
        
        return filtered_results
    finally:
        await client.close()


# =============================================================================
# 테이블 조회
# =============================================================================

async def fetch_schema_tables(
    search: Optional[str] = None,
    schema: Optional[str] = None,
    limit: int = 100
) -> list:
    """테이블 목록 조회
    
    Args:
        search: 테이블명/설명 검색
        schema: 스키마 필터
        limit: 결과 제한
        
    Returns:
        테이블 정보 리스트
    """
    client = Neo4jClient()
    try:
        where_conditions = []
        params = {"limit": limit}
        
        if schema:
            where_conditions.append("__cy_t__.schema = $schema")
            params["schema"] = schema
        
        if search:
            where_conditions.append(
                "(toLower(__cy_t__.name) CONTAINS toLower($search) "
                "OR toLower(__cy_t__.description) CONTAINS toLower($search))"
            )
            params["search"] = search
        
        where_clause = " AND ".join(where_conditions) if where_conditions else "true"
        
        query = f"""
            MATCH (__cy_t__:Table)
            WHERE {where_clause}
            OPTIONAL MATCH (__cy_t__)-[:HAS_COLUMN]->(__cy_c__:Column)
            WITH __cy_t__, count(__cy_c__) AS col_count
            RETURN __cy_t__.name AS name,
                   __cy_t__.schema AS schema,
                   __cy_t__.datasource AS datasource,
                   __cy_t__.description AS description,
                   __cy_t__.description_source AS description_source,
                   __cy_t__.analyzed_description AS analyzed_description,
                   col_count AS column_count
            ORDER BY __cy_t__.datasource, __cy_t__.schema, __cy_t__.name
            LIMIT {limit}
        """
        
        results = await client.execute_queries([query])
        return results[0] if results else []
    finally:
        await client.close()


async def fetch_table_columns(
    table_name: str,
    schema: str = ""
) -> list:
    """테이블 컬럼 목록 조회
    
    Args:
        table_name: 테이블명
        schema: 스키마명
        
    Returns:
        컬럼 정보 리스트
    """
    client = Neo4jClient()
    try:
        params = {"table_name": table_name}
        
        if schema:
            where_clause = "__cy_t__.name = $table_name AND __cy_t__.schema = $schema"
            params["schema"] = schema
        else:
            where_clause = "(__cy_t__.name = $table_name OR __cy_t__.fqn ENDS WITH $table_name)"
        
        query = {
            "query": f"""
                MATCH (__cy_t__:Table)-[:HAS_COLUMN]->(__cy_c__:Column)
                WHERE {where_clause}
                RETURN __cy_c__.name AS name,
                       __cy_t__.name AS table_name,
                       __cy_c__.dtype AS dtype,
                       __cy_c__.nullable AS nullable,
                       __cy_c__.description AS description,
                       __cy_c__.description_source AS description_source,
                       __cy_c__.analyzed_description AS analyzed_description
                ORDER BY __cy_c__.name
            """,
            "parameters": params
        }
        
        results = await client.execute_queries([query])
        return results[0] if results else []
    finally:
        await client.close()


# =============================================================================
# 관계 조회/생성/삭제
# =============================================================================

async def fetch_schema_relationships() -> list:
    """테이블 관계 목록 조회
    
    모든 관계 타입을 조회합니다:
    - FK_TO_TABLE: 기본 FK 관계
    - ONE_TO_ONE: 1:1 관계
    - ONE_TO_MANY: 1:N 관계
    - MANY_TO_ONE: N:1 관계
    - MANY_TO_MANY: N:N 관계
    
    Returns:
        관계 정보 리스트
    """
    client = Neo4jClient()
    try:
        query = """
            MATCH (__cy_t1__:Table)-[__cy_r__]->(__cy_t2__:Table)
            WHERE type(__cy_r__) IN ['FK_TO_TABLE', 'ONE_TO_ONE', 'ONE_TO_MANY', 'MANY_TO_ONE', 'MANY_TO_MANY']
            RETURN __cy_t1__.name AS from_table,
                   __cy_t1__.schema AS from_schema,
                   __cy_r__.sourceColumn AS from_column,
                   __cy_t2__.name AS to_table,
                   __cy_t2__.schema AS to_schema,
                   __cy_r__.targetColumn AS to_column,
                   type(__cy_r__) AS relationship_type,
                   __cy_r__.description AS description
            ORDER BY __cy_t1__.name, __cy_t2__.name
        """
        
        results = await client.execute_queries([query])
        return results[0] if results else []
    finally:
        await client.close()


async def create_schema_relationship(
    from_table: str,
    from_schema: str,
    from_column: str,
    to_table: str,
    to_schema: str,
    to_column: str,
    relationship_type: str = "FK_TO_TABLE",
    description: str = ""
) -> dict:
    """테이블 관계 생성
    
    Returns:
        생성된 관계 정보
    """
    client = Neo4jClient()
    try:
        # 관계 타입은 동적이므로 f-string 사용하되, 다른 값들은 파라미터화
        query = {
            "query": f"""
                MATCH (__cy_t1__:Table {{name: $from_table}})
                MATCH (__cy_t2__:Table {{name: $to_table}})
                MERGE (__cy_t1__)-[__cy_r__:{relationship_type}]->(__cy_t2__)
                SET __cy_r__.sourceColumn = $from_column,
                    __cy_r__.targetColumn = $to_column,
                    __cy_r__.description = $description,
                    __cy_r__.source = 'user'
                RETURN __cy_t1__.name AS from_table, __cy_t2__.name AS to_table
            """,
            "parameters": {
                "from_table": from_table,
                "to_table": to_table,
                "from_column": from_column,
                "to_column": to_column,
                "description": description,
            }
        }
        
        results = await client.execute_queries([query])
        
        if results and results[0]:
            return {"message": "관계가 생성되었습니다.", "created": True}
        else:
            raise HTTPException(404, "테이블을 찾을 수 없습니다.")
    finally:
        await client.close()


async def delete_schema_relationship(
    from_table: str,
    from_column: str,
    to_table: str,
    to_column: str
) -> dict:
    """테이블 관계 삭제
    
    모든 관계 타입을 삭제합니다 (FK_TO_TABLE, ONE_TO_ONE, ONE_TO_MANY, MANY_TO_ONE, MANY_TO_MANY)
    
    Returns:
        삭제 결과
    """
    client = Neo4jClient()
    try:
        query = f"""
            MATCH (__cy_t1__:Table {{name: '{escape_for_cypher(from_table)}'}})-[__cy_r__]->(__cy_t2__:Table {{name: '{escape_for_cypher(to_table)}'}})
            WHERE type(__cy_r__) IN ['FK_TO_TABLE', 'ONE_TO_ONE', 'ONE_TO_MANY', 'MANY_TO_ONE', 'MANY_TO_MANY']
              AND __cy_r__.sourceColumn = '{escape_for_cypher(from_column)}' 
              AND __cy_r__.targetColumn = '{escape_for_cypher(to_column)}'
            DELETE __cy_r__
            RETURN count(*) AS deleted
        """
        
        results = await client.execute_queries([query])
        deleted = results[0][0]["deleted"] if results and results[0] else 0
        
        return {"message": f"{deleted}개 관계가 삭제되었습니다.", "deleted": deleted}
    finally:
        await client.close()


# =============================================================================
# 프로시저 참조/Statement 조회
# =============================================================================

async def fetch_table_references(
    table_name: str,
    schema: str = "",
    column_name: Optional[str] = None
) -> list:
    """테이블 또는 컬럼이 참조된 프로시저 목록 조회
    
    Returns:
        프로시저 참조 정보 리스트
    """
    client = Neo4jClient()
    try:
        query = {
            "query": """
                MATCH (__cy_s__)-[:FROM|WRITES]->(__cy_t__:Table)
                WHERE __cy_t__.name = $table_name OR __cy_t__.fqn ENDS WITH $table_name
                OPTIONAL MATCH (__cy_p__)-[:PARENT_OF*]->(__cy_s__)
                WHERE __cy_p__:PROCEDURE OR __cy_p__:FUNCTION
                RETURN DISTINCT __cy_p__.name AS procedure_name,
                       labels(__cy_p__)[0] AS procedure_type,
                       __cy_s__.start_line AS start_line,
                       type(()-[__cy_r__]->(__cy_t__))[0] AS access_type,
                       __cy_s__.type AS statement_type,
                       __cy_s__.start_line AS statement_line,
                       __cy_p__.file_name AS file_name,
                       __cy_p__.file_directory AS file_directory
                ORDER BY __cy_p__.name, __cy_s__.start_line
            """,
            "parameters": {"table_name": table_name}
        }
        
        results = await client.execute_queries([query])
        return results[0] if results else []
    finally:
        await client.close()


async def fetch_procedure_statements(
    procedure_name: str,
    file_directory: Optional[str] = None
) -> list:
    """프로시저의 모든 Statement와 AI 설명 조회
    
    Returns:
        Statement 정보 리스트
    """
    client = Neo4jClient()
    try:
        params = {"procedure_name": procedure_name}
        where_clause = "__cy_p__.name = $procedure_name"
        
        if file_directory:
            where_clause += " AND __cy_p__.file_directory = $file_directory"
            params["file_directory"] = file_directory
        
        query = {
            "query": f"""
                MATCH (__cy_p__)-[:PARENT_OF*]->(__cy_s__)
                WHERE ({where_clause}) AND (__cy_s__:Statement OR __cy_s__.type IS NOT NULL)
                RETURN __cy_s__.start_line AS start_line,
                       __cy_s__.end_line AS end_line,
                       __cy_s__.type AS statement_type,
                       __cy_s__.summary AS summary,
                       __cy_s__.ai_description AS ai_description
                ORDER BY __cy_s__.start_line
            """,
            "parameters": params
        }
        
        results = await client.execute_queries([query])
        return results[0] if results else []
    finally:
        await client.close()


# =============================================================================
# 설명 업데이트
# =============================================================================

async def update_table_description(
    table_name: str,
    schema: str,
    description: str,
    api_key: str = None
) -> dict:
    """테이블 설명 업데이트 및 재벡터화
    
    Returns:
        업데이트 결과
    """
    client = Neo4jClient()
    try:
        query = {
            "query": """
                MATCH (__cy_t__:Table)
                WHERE __cy_t__.name = $table_name 
                  AND (__cy_t__.schema = $schema OR __cy_t__.schema IS NULL)
                SET __cy_t__.description = $description,
                    __cy_t__.description_source = 'user'
                RETURN __cy_t__.name AS name
            """,
            "parameters": {
                "table_name": table_name,
                "schema": schema,
                "description": description,
            }
        }
        
        results = await client.execute_queries([query])
        
        if not results or not results[0]:
            raise HTTPException(404, "테이블을 찾을 수 없습니다.")
        
        # 임베딩 재생성 (API 키가 있는 경우)
        if api_key:
            try:
                from openai import AsyncOpenAI
                openai_client = AsyncOpenAI(api_key=api_key)
                embedding_client = EmbeddingClient(openai_client)
                
                embedding = await embedding_client.embed_text(description)
                
                if embedding:
                    embedding_query = {
                        "query": """
                            MATCH (__cy_t__:Table {name: $table_name})
                            SET __cy_t__.embedding = $embedding
                        """,
                        "parameters": {
                            "table_name": table_name,
                            "embedding": embedding,
                        }
                    }
                    await client.execute_queries([embedding_query])
            except Exception as e:
                error_msg = f"임베딩 업데이트 실패: {e}"
                logger.error(error_msg)
                raise RuntimeError(error_msg) from e
        
        return {"message": "테이블 설명이 업데이트되었습니다.", "updated": True}
    finally:
        await client.close()


async def update_column_description(
    table_name: str,
    table_schema: str,
    column_name: str,
    description: str,
    api_key: str = None
) -> dict:
    """컬럼 설명 업데이트 및 재벡터화
    
    Returns:
        업데이트 결과
    """
    client = Neo4jClient()
    try:
        query = {
            "query": """
                MATCH (__cy_t__:Table)-[:HAS_COLUMN]->(__cy_c__:Column)
                WHERE __cy_t__.name = $table_name AND __cy_c__.name = $column_name
                SET __cy_c__.description = $description,
                    __cy_c__.description_source = 'user'
                RETURN __cy_c__.name AS name
            """,
            "parameters": {
                "table_name": table_name,
                "column_name": column_name,
                "description": description,
            }
        }
        
        results = await client.execute_queries([query])
        
        if not results or not results[0]:
            raise HTTPException(404, "컬럼을 찾을 수 없습니다.")
        
        return {"message": "컬럼 설명이 업데이트되었습니다.", "updated": True}
    finally:
        await client.close()


# =============================================================================
# 벡터라이징
# =============================================================================

async def vectorize_schema_tables(
    db_name: str = "postgres",
    schema: Optional[str] = None,
    include_tables: bool = True,
    include_columns: bool = True,
    reembed_existing: bool = False,
    batch_size: int = 100,
    api_key: str = None
) -> dict:
    """전체 스키마 벡터화
    
    Args:
        api_key: OpenAI API 키 (필수)
    
    Returns:
        벡터화 결과 통계
    """
    if not api_key:
        raise HTTPException(400, {"error": "OpenAI API 키가 필요합니다."})
    
    from openai import AsyncOpenAI
    openai_client = AsyncOpenAI(api_key=api_key)
    embedding_client = EmbeddingClient(openai_client)
    
    client = Neo4jClient()
    stats = {"tables_processed": 0, "columns_processed": 0, "errors": 0}
    
    try:
        if include_tables:
            # 테이블 벡터화
            where_conditions = []
            params = {"batch_size": batch_size}
            
            if schema:
                where_conditions.append("__cy_t__.schema = $schema")
                params["schema"] = schema
            if not reembed_existing:
                where_conditions.append("__cy_t__.embedding IS NULL")
            
            where_clause = "WHERE " + " AND ".join(where_conditions) if where_conditions else ""
            
            query = {
                "query": f"""
                    MATCH (__cy_t__:Table)
                    {where_clause}
                    RETURN __cy_t__.name AS name, 
                           __cy_t__.description AS description,
                           __cy_t__.schema AS schema
                    LIMIT $batch_size
                """,
                "parameters": params
            }
            
            results = await client.execute_queries([query])
            tables = results[0] if results else []
            
            for table in tables:
                try:
                    text = EmbeddingClient.format_table_text(
                        table["name"],
                        table.get("description") or ""
                    )
                    embedding = await embedding_client.embed_text(text)
                    
                    if embedding:
                        update_query = {
                            "query": """
                                MATCH (__cy_t__:Table {name: $table_name})
                                SET __cy_t__.embedding = $embedding
                            """,
                            "parameters": {
                                "table_name": table["name"],
                                "embedding": embedding,
                            }
                        }
                        await client.execute_queries([update_query])
                        stats["tables_processed"] += 1
                except Exception as e:
                    error_msg = f"테이블 '{table['name']}' 벡터화 실패: {e}"
                    logger.error(error_msg)
                    raise RuntimeError(error_msg) from e
        
        return {
            "message": "벡터화가 완료되었습니다.",
            "stats": stats
        }
    finally:
        await client.close()

