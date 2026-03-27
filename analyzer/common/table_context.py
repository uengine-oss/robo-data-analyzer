"""테이블 컨텍스트 수집 — GWT 생성용

DBMS/Framework 양쪽에서 사용하는 테이블 컬럼 메타 + 선택적 샘플 데이터 수집.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional

from client.neo4j_client import Neo4jClient

logger = logging.getLogger(__name__)


async def fetch_table_context(
    client: Neo4jClient,
    table_names: List[str],
    datasource_name: str = "",
) -> str:
    """테이블의 컬럼 메타 + 선택적 샘플 데이터를 문자열로 반환.

    1. Neo4j에서 컬럼 메타 조회 (항상)
    2. datasource가 있으면 text2sql API로 샘플 5행 조회 (선택적)

    Returns:
        LLM 프롬프트에 삽입할 수 있는 테이블 컨텍스트 문자열
    """
    if not table_names:
        return ""

    unique_tables = list(dict.fromkeys(table_names))[:10]  # 최대 10개
    parts: List[str] = []

    # 1. Neo4j에서 컬럼 메타 조회
    for table_name in unique_tables:
        columns = await _fetch_column_meta(client, table_name)
        if not columns:
            continue

        col_strs = []
        for c in columns:
            flags = []
            if c.get("is_primary_key"):
                flags.append("PK")
            if c.get("nullable") is False:
                flags.append("NOT NULL")
            dtype = c.get("dtype", "")
            flag_str = f", {', '.join(flags)}" if flags else ""
            desc = f" — {c['description']}" if c.get("description") else ""
            col_strs.append(f"{c['name']}({dtype}{flag_str}){desc}")

        section = f"[{table_name} 테이블]\n컬럼: {', '.join(col_strs)}"

        # 2. 샘플 데이터 (datasource 있을 때만)
        if datasource_name:
            samples = await _fetch_sample_rows(table_name, datasource_name)
            if samples:
                sample_strs = [str(row) for row in samples[:5]]
                section += f"\n샘플: {' | '.join(sample_strs)}"

        parts.append(section)

    return "\n\n".join(parts)


async def _fetch_column_meta(
    client: Neo4jClient,
    table_name: str,
) -> List[Dict[str, Any]]:
    """Neo4j에서 테이블의 컬럼 메타데이터를 조회."""
    query = (
        "MATCH (t:Table)-[:HAS_COLUMN]->(c:Column)\n"
        "WHERE t.name = $name OR t.fqn ENDS WITH $name\n"
        "RETURN c.name AS name, c.dtype AS dtype, c.nullable AS nullable, "
        "c.is_primary_key AS is_primary_key, c.description AS description\n"
        "ORDER BY c.name"
    )
    try:
        results = await client.execute_queries([query], {"name": table_name})
        return results[0] if results and results[0] else []
    except Exception as e:
        logger.debug("[TABLE_CTX] 컬럼 메타 조회 실패 (%s): %s", table_name, e)
        return []


async def _fetch_sample_rows(
    table_name: str,
    datasource_name: str,
    limit: int = 5,
) -> List[Dict[str, Any]]:
    """text2sql API에서 테이블 샘플 행을 조회 (선택적)."""
    import aiohttp

    text2sql_url = os.environ.get("TEXT2SQL_API_URL", "http://localhost:8000")
    url = f"{text2sql_url}/text2sql/direct-sql"

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                url,
                json={
                    "datasource": datasource_name,
                    "sql": f"SELECT * FROM {table_name} LIMIT {limit}",
                    "skip_ai": True,
                },
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data.get("rows", [])[:limit]
    except Exception as e:
        logger.debug("[TABLE_CTX] 샘플 조회 실패 (%s): %s", table_name, e)

    return []
