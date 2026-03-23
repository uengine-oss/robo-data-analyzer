"""스키마 관리 서비스

테이블, 컬럼, 관계 등 스키마 CRUD 기능을 제공합니다.

섹션 구성:
    1. 시멘틱 검색
    2. 테이블/컬럼 조회
    3. 참조(프로시저·프레임워크) 조회
    4. 관계 조회 / 생성 / 삭제
    5. 설명 업데이트
    6. 벡터라이징
"""

import logging
from typing import Optional

import numpy as np
from fastapi import HTTPException
from openai import AsyncOpenAI

from client.embedding_client import EmbeddingClient
from client.neo4j_client import Neo4jClient

logger = logging.getLogger(__name__)


# =============================================================================
# 1. 시멘틱 검색
# =============================================================================

async def search_tables_by_semantic(
    query: str,
    limit: int,
    api_key: str,
) -> list:
    """시멘틱 검색: 테이블 설명의 의미적 유사도 기반 검색

    Args:
        query: 검색 쿼리
        limit: 결과 제한
        api_key: OpenAI API 키 (필수)

    Returns:
        [{"name", "schema", "description", "similarity"}, ...]
    """
    if not api_key:
        raise HTTPException(400, {"error": "OpenAI API 키가 필요합니다."})

    client = Neo4jClient()

    try:
        openai_client = AsyncOpenAI(api_key=api_key)
    except Exception as e:
        raise HTTPException(400, {"error": f"OpenAI API 키가 유효하지 않습니다: {e}"})

    try:
        # -- 설명이 있는 테이블 최대 200개 조회 --
        cypher_query = """
            MATCH (t:Table)
            WHERE t.description IS NOT NULL
              AND t.description <> ''
            RETURN t.name        AS name,
                   t.schema      AS schema,
                   t.description AS description
            ORDER BY t.name
            LIMIT 200
        """

        results = await client.execute_queries([cypher_query])
        records = results[0] if results else []
        if not records:
            return []

        # -- 쿼리 임베딩 생성 --
        query_response = await openai_client.embeddings.create(
            model="text-embedding-3-small",
            input=query,
        )
        query_embedding = np.array(query_response.data[0].embedding)

        # -- 테이블 설명 임베딩 생성 --
        descriptions = [
            (r.get("description") or "no description")[:500]
            for r in records
        ]
        desc_response = await openai_client.embeddings.create(
            model="text-embedding-3-small",
            input=descriptions,
        )

        # -- 코사인 유사도 계산 --
        results_with_similarity = []
        for i, record in enumerate(records):
            desc_embedding = np.array(desc_response.data[i].embedding)

            norm_product = (
                np.linalg.norm(query_embedding) * np.linalg.norm(desc_embedding)
            )
            similarity = (
                0.0 if norm_product == 0
                else float(np.dot(query_embedding, desc_embedding) / norm_product)
            )

            results_with_similarity.append({
                "name": record["name"],
                "schema": record["schema"] or "public",
                "description": record["description"][:200],
                "similarity": round(similarity, 4),
            })

        results_with_similarity.sort(key=lambda x: x["similarity"], reverse=True)
        return [r for r in results_with_similarity[:limit] if r["similarity"] >= 0.3]
    finally:
        await client.close()


# =============================================================================
# 2. 테이블 / 컬럼 조회
# =============================================================================

async def fetch_schema_tables(
    search: Optional[str] = None,
    schema: Optional[str] = None,
    limit: int = 100,
) -> list:
    """테이블 목록 조회

    Args:
        search: 테이블명/설명 검색
        schema: 스키마 필터
        limit: 결과 제한
    """
    client = Neo4jClient()
    try:
        where_conditions: list[str] = []
        params: dict = {"limit": limit}

        if schema:
            where_conditions.append("t.schema = $schema")
            params["schema"] = schema
        if search:
            where_conditions.append(
                "(toLower(t.name) CONTAINS toLower($search) "
                "OR toLower(t.description) CONTAINS toLower($search))"
            )
            params["search"] = search

        where_clause = " AND ".join(where_conditions) if where_conditions else "true"

        query = f"""
            MATCH (t:Table)
            WHERE {where_clause}
            OPTIONAL MATCH (t)-[:HAS_COLUMN]->(c:Column)
            WITH t, count(c) AS col_count
            RETURN t.name                 AS name,
                   t.schema               AS schema,
                   t.datasource           AS datasource,
                   t.description          AS description,
                   t.description_source   AS description_source,
                   t.analyzed_description AS analyzed_description,
                   col_count              AS column_count
            ORDER BY t.datasource, t.schema, t.name
            LIMIT {limit}
        """

        results = await client.execute_queries([query])
        return results[0] if results else []
    finally:
        await client.close()


async def fetch_table_columns(
    table_name: str,
    schema: str = "",
) -> list:
    """테이블 컬럼 목록 조회

    Args:
        table_name: 테이블명
        schema: 스키마명
    """
    client = Neo4jClient()
    try:
        params: dict = {"table_name": table_name}

        if schema:
            where_clause = "t.name = $table_name AND t.schema = $schema"
            params["schema"] = schema
        else:
            where_clause = "(t.name = $table_name OR t.fqn ENDS WITH $table_name)"

        query = {
            "query": f"""
                MATCH (t:Table)-[:HAS_COLUMN]->(c:Column)
                WHERE {where_clause}
                RETURN c.name                 AS name,
                       t.name                 AS table_name,
                       c.dtype                AS dtype,
                       COALESCE(c.nullable, true) AS nullable,
                       c.description          AS description,
                       c.description_source   AS description_source,
                       c.analyzed_description AS analyzed_description
                ORDER BY c.name
            """,
            "parameters": params,
        }

        results = await client.execute_queries([query])
        return results[0] if results else []
    finally:
        await client.close()


# =============================================================================
# 3. 참조 조회 (프로시저 / 프레임워크)
# =============================================================================

async def fetch_table_references(
    table_name: str,
    schema: str = "",
    column_name: Optional[str] = None,
) -> dict:
    """테이블 또는 컬럼이 참조된 프로시저 + 프레임워크 목록 조회

    Returns:
        {"references": [...], "framework_references": [...]}
    """
    client = Neo4jClient()
    try:
        # ── (A) 프로시저 전략 참조 ──
        #   Statement -[:FROM|WRITES]-> Table 관계를 역추적하여
        #   상위 PROCEDURE/FUNCTION 노드를 찾는다.
        proc_query = {
            "query": """
                MATCH (s)-[:FROM|WRITES]->(t:Table)
                WHERE t.name = $table_name
                   OR t.fqn ENDS WITH $table_name
                OPTIONAL MATCH (p)-[:PARENT_OF*]->(s)
                    WHERE p:PROCEDURE OR p:FUNCTION
                RETURN DISTINCT
                    p.name           AS procedure_name,
                    labels(p)[0]     AS procedure_type,
                    s.start_line     AS start_line,
                    s.type           AS statement_type,
                    s.start_line     AS statement_line,
                    p.file_name      AS file_name,
                    p.file_directory AS file_directory
                ORDER BY p.name, s.start_line
            """,
            "parameters": {"table_name": table_name},
        }

        # ── (B) 프레임워크 전략 참조 ──
        #   FUNCTION/VARIABLE -[:READS|WRITES]-> Table
        #   MODULE -[:REFER_TO]-> Table
        fw_query = {
            "query": """
                MATCH (src)-[r:READS|WRITES]->(t:Table)
                WHERE (t.name = $table_name OR t.fqn ENDS WITH $table_name)
                  AND (src:FUNCTION OR src:VARIABLE)
                OPTIONAL MATCH (m:MODULE)-[:CONTAINS*]->(src)
                RETURN DISTINCT
                    COALESCE(src.name, src.function_id) AS source_name,
                    'FUNCTION'       AS source_type,
                    type(r)          AS access_type,
                    src.start_line   AS start_line,
                    src.file_name    AS file_name,
                    src.file_directory AS file_directory,
                    m.name           AS module_name
                ORDER BY source_name

                UNION ALL

                MATCH (m:MODULE)-[r:REFER_TO]->(t:Table)
                WHERE t.name = $table_name
                   OR t.fqn ENDS WITH $table_name
                RETURN DISTINCT
                    m.name           AS source_name,
                    'MODULE'         AS source_type,
                    'REFER_TO'       AS access_type,
                    m.start_line     AS start_line,
                    m.file_name      AS file_name,
                    m.file_directory AS file_directory,
                    m.name           AS module_name
                ORDER BY source_name
            """,
            "parameters": {"table_name": table_name},
        }

        results = await client.execute_queries([proc_query, fw_query])
        proc_records = results[0] if len(results) > 0 else []
        fw_records = results[1] if len(results) > 1 else []

        return {
            "references": proc_records,
            "framework_references": fw_records,
        }
    finally:
        await client.close()


async def fetch_procedure_statements(
    procedure_name: str,
    file_directory: Optional[str] = None,
) -> list:
    """프로시저의 모든 Statement와 AI 설명 조회"""
    client = Neo4jClient()
    try:
        params: dict = {"procedure_name": procedure_name}
        where_clause = "p.name = $procedure_name"

        if file_directory:
            where_clause += " AND p.file_directory = $file_directory"
            params["file_directory"] = file_directory

        query = {
            "query": f"""
                MATCH (p)-[:PARENT_OF*]->(s)
                WHERE ({where_clause})
                  AND (s:Statement OR s.type IS NOT NULL)
                RETURN s.start_line     AS start_line,
                       s.end_line       AS end_line,
                       s.type           AS statement_type,
                       s.summary        AS summary,
                       s.ai_description AS ai_description
                ORDER BY s.start_line
            """,
            "parameters": params,
        }

        results = await client.execute_queries([query])
        return results[0] if results else []
    finally:
        await client.close()


# =============================================================================
# 4. 관계 조회 / 생성 / 삭제
# =============================================================================

_RELATIONSHIP_TYPES = [
    "FK_TO_TABLE",
    "ONE_TO_ONE",
    "ONE_TO_MANY",
    "MANY_TO_ONE",
    "MANY_TO_MANY",
]


async def fetch_schema_relationships() -> list:
    """테이블 관계 목록 조회

    대상 관계 타입: FK_TO_TABLE, ONE_TO_ONE, ONE_TO_MANY, MANY_TO_ONE, MANY_TO_MANY
    """
    client = Neo4jClient()
    try:
        query = """
            MATCH (t1:Table)-[r]->(t2:Table)
            WHERE type(r) IN ['FK_TO_TABLE', 'ONE_TO_ONE',
                              'ONE_TO_MANY', 'MANY_TO_ONE', 'MANY_TO_MANY']
            RETURN t1.name       AS from_table,
                   t1.schema     AS from_schema,
                   r.sourceColumn AS from_column,
                   t2.name       AS to_table,
                   t2.schema     AS to_schema,
                   r.targetColumn AS to_column,
                   type(r)       AS relationship_type,
                   r.description AS description
            ORDER BY t1.name, t2.name
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
    description: str = "",
) -> dict:
    """테이블 관계 생성"""
    client = Neo4jClient()
    try:
        # NOTE: 관계 타입은 Cypher 파라미터로 전달 불가 → f-string 사용
        query = {
            "query": f"""
                MATCH (t1:Table {{name: $from_table}})
                MATCH (t2:Table {{name: $to_table}})
                MERGE (t1)-[r:{relationship_type}]->(t2)
                SET r.sourceColumn = $from_column,
                    r.targetColumn = $to_column,
                    r.description  = $description,
                    r.source       = 'user'
                RETURN t1.name AS from_table,
                       t2.name AS to_table
            """,
            "parameters": {
                "from_table": from_table,
                "to_table": to_table,
                "from_column": from_column,
                "to_column": to_column,
                "description": description,
            },
        }

        results = await client.execute_queries([query])
        if results and results[0]:
            return {"message": "관계가 생성되었습니다.", "created": True}
        raise HTTPException(404, "테이블을 찾을 수 없습니다.")
    finally:
        await client.close()


async def delete_schema_relationship(
    from_table: str,
    from_column: str,
    to_table: str,
    to_column: str,
) -> dict:
    """테이블 관계 삭제 (모든 관계 타입 대상)"""
    client = Neo4jClient()
    try:
        # NOTE: escape_for_cypher 는 현재 미정의 — 파라미터화 전환 필요 (TODO)
        query = f"""
            MATCH (t1:Table {{name: '{escape_for_cypher(from_table)}'}})
                  -[r]->
                  (t2:Table {{name: '{escape_for_cypher(to_table)}'}})
            WHERE type(r) IN ['FK_TO_TABLE', 'ONE_TO_ONE',
                              'ONE_TO_MANY', 'MANY_TO_ONE', 'MANY_TO_MANY']
              AND r.sourceColumn = '{escape_for_cypher(from_column)}'
              AND r.targetColumn = '{escape_for_cypher(to_column)}'
            DELETE r
            RETURN count(*) AS deleted
        """

        results = await client.execute_queries([query])
        deleted = results[0][0]["deleted"] if results and results[0] else 0
        return {"message": f"{deleted}개 관계가 삭제되었습니다.", "deleted": deleted}
    finally:
        await client.close()


# =============================================================================
# 5. 설명 업데이트
# =============================================================================

async def update_table_description(
    table_name: str,
    schema: str,
    description: str,
    api_key: str = None,
) -> dict:
    """테이블 설명 업데이트 및 재벡터화"""
    client = Neo4jClient()
    try:
        query = {
            "query": """
                MATCH (t:Table)
                WHERE t.name = $table_name
                  AND (t.schema = $schema OR t.schema IS NULL)
                SET t.description        = $description,
                    t.description_source  = 'user'
                RETURN t.name AS name
            """,
            "parameters": {
                "table_name": table_name,
                "schema": schema,
                "description": description,
            },
        }

        results = await client.execute_queries([query])
        if not results or not results[0]:
            raise HTTPException(404, "테이블을 찾을 수 없습니다.")

        # -- 임베딩 재생성 (API 키가 있는 경우) --
        if api_key:
            try:
                openai_client = AsyncOpenAI(api_key=api_key)
                embedding_client = EmbeddingClient(openai_client)
                embedding = await embedding_client.embed_text(description)

                if embedding:
                    embedding_query = {
                        "query": """
                            MATCH (t:Table {name: $table_name})
                            SET t.embedding = $embedding
                        """,
                        "parameters": {
                            "table_name": table_name,
                            "embedding": embedding,
                        },
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
    api_key: str = None,
) -> dict:
    """컬럼 설명 업데이트"""
    client = Neo4jClient()
    try:
        query = {
            "query": """
                MATCH (t:Table)-[:HAS_COLUMN]->(c:Column)
                WHERE t.name = $table_name
                  AND c.name = $column_name
                SET c.description        = $description,
                    c.description_source  = 'user'
                RETURN c.name AS name
            """,
            "parameters": {
                "table_name": table_name,
                "column_name": column_name,
                "description": description,
            },
        }

        results = await client.execute_queries([query])
        if not results or not results[0]:
            raise HTTPException(404, "컬럼을 찾을 수 없습니다.")

        return {"message": "컬럼 설명이 업데이트되었습니다.", "updated": True}
    finally:
        await client.close()


# =============================================================================
# 6. 벡터라이징
# =============================================================================

async def vectorize_schema_tables(
    db_name: str = "postgres",
    schema: Optional[str] = None,
    include_tables: bool = True,
    include_columns: bool = True,
    reembed_existing: bool = False,
    batch_size: int = 100,
    api_key: str = None,
) -> dict:
    """전체 스키마 벡터화

    Args:
        api_key: OpenAI API 키 (필수)

    Returns:
        벡터화 결과 통계
    """
    if not api_key:
        raise HTTPException(400, {"error": "OpenAI API 키가 필요합니다."})

    openai_client = AsyncOpenAI(api_key=api_key)
    embedding_client = EmbeddingClient(openai_client)

    client = Neo4jClient()
    stats = {"tables_processed": 0, "columns_processed": 0, "errors": 0}

    try:
        if include_tables:
            # -- 벡터화 대상 테이블 조회 --
            where_conditions: list[str] = []
            params: dict = {"batch_size": batch_size}

            if schema:
                where_conditions.append("t.schema = $schema")
                params["schema"] = schema
            if not reembed_existing:
                where_conditions.append("t.embedding IS NULL")

            where_clause = (
                "WHERE " + " AND ".join(where_conditions)
                if where_conditions else ""
            )

            query = {
                "query": f"""
                    MATCH (t:Table)
                    {where_clause}
                    RETURN t.name        AS name,
                           t.description AS description,
                           t.schema      AS schema
                    LIMIT $batch_size
                """,
                "parameters": params,
            }

            results = await client.execute_queries([query])
            tables = results[0] if results else []

            # -- 테이블별 임베딩 생성 및 저장 --
            for table in tables:
                try:
                    text = EmbeddingClient.format_table_text(
                        table["name"],
                        table.get("description") or "",
                    )
                    embedding = await embedding_client.embed_text(text)

                    if embedding:
                        update_query = {
                            "query": """
                                MATCH (t:Table {name: $table_name})
                                SET t.embedding = $embedding
                            """,
                            "parameters": {
                                "table_name": table["name"],
                                "embedding": embedding,
                            },
                        }
                        await client.execute_queries([update_query])
                        stats["tables_processed"] += 1
                except Exception as e:
                    error_msg = f"테이블 '{table['name']}' 벡터화 실패: {e}"
                    logger.error(error_msg)
                    raise RuntimeError(error_msg) from e

        return {"message": "벡터화가 완료되었습니다.", "stats": stats}
    finally:
        await client.close()
