"""테이블 컨텍스트 수집 — 비즈니스 로직 시나리오 생성용

DBMS/Framework 양쪽에서 사용하는 테이블 컬럼 메타 + 선택적 샘플 데이터 수집.

데이터 소스 우선순위:
1. TEST_SAMPLE_DATA_PATH 환경변수 → JSON fixture 파일에서 로드 (테스트용)
2. datasource_name 지정 → text2sql API로 샘플 조회 (실서비스)
3. 둘 다 없으면 → Neo4j 컬럼 메타만 반환
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, List, Optional

from client.neo4j_client import Neo4jClient

logger = logging.getLogger(__name__)

# fixture 캐시 (경로별 1회만 로드)
_fixture_cache: Dict[str, Any] = {}
_fixture_loaded_path: Optional[str] = None


def _load_fixture() -> Dict[str, Any]:
    """TEST_SAMPLE_DATA_PATH 환경변수에 지정된 JSON fixture 로드."""
    global _fixture_cache, _fixture_loaded_path

    path = os.environ.get("TEST_SAMPLE_DATA_PATH", "")
    if not path:
        return {}

    # 같은 경로면 캐시 반환
    if path == _fixture_loaded_path:
        return _fixture_cache

    if not os.path.isfile(path):
        logger.error("[TABLE_CTX] fixture 파일 없음 | path=%s", path)
        return {}

    try:
        with open(path, "r", encoding="utf-8") as f:
            _fixture_cache = json.load(f)
        _fixture_loaded_path = path
        logger.info("[TABLE_CTX] fixture 로드 | path=%s tables=%d", path, len(_fixture_cache))
    except Exception as e:
        logger.error("[TABLE_CTX] fixture 로드 실패 (%s): %s", path, e)
        _fixture_cache = {}

    return _fixture_cache


def _build_section_from_fixture(table_name: str, fixture: Dict[str, Any]) -> Optional[str]:
    """fixture 데이터에서 테이블 컨텍스트 문자열 생성."""
    table_data = fixture.get(table_name)
    if not table_data:
        return None

    columns = table_data.get("columns", [])
    col_strs = []
    for c in columns:
        flags = []
        if c.get("is_primary_key"):
            flags.append("PK")
        if c.get("nullable") is False:
            flags.append("NOT NULL")
        dtype = c.get("dtype", "")
        flag_str = f", {', '.join(flags)}" if flags else ""
        col_strs.append(f"{c['name']}({dtype}{flag_str})")

    section = f"[{table_name} 테이블]\n컬럼: {', '.join(col_strs)}"

    sample_rows = table_data.get("sample_rows", [])
    if sample_rows:
        sample_strs = [str(row) for row in sample_rows[:5]]
        section += f"\n샘플: {' | '.join(sample_strs)}"

    return section


async def fetch_table_context(
    client: Neo4jClient,
    table_names: List[str],
    datasource_name: str = "",
) -> str:
    """테이블의 컬럼 메타 + 선택적 샘플 데이터를 문자열로 반환.

    Returns:
        LLM 프롬프트에 삽입할 수 있는 테이블 컨텍스트 문자열
    """
    if not table_names:
        return ""

    unique_tables = list(dict.fromkeys(table_names))[:10]
    parts: List[str] = []

    # 1. fixture 파일이 있으면 우선 사용 (테스트용)
    fixture = _load_fixture()
    if fixture:
        for table_name in unique_tables:
            section = _build_section_from_fixture(table_name, fixture)
            if section:
                parts.append(section)
        if parts:
            logger.info("[TABLE_CTX] fixture 기반 컨텍스트 생성 | tables=%s len=%d", unique_tables, len("\n\n".join(parts)))
        return "\n\n".join(parts)

    # 2. Neo4j 컬럼 메타 + 선택적 datasource 샘플
    for table_name in unique_tables:
        columns = await _fetch_column_meta(client, table_name)
        if not columns:
            continue

        col_strs = []
        for c in columns:
            flags = []
            if c.get("is_primary_key"):
                flags.append("PK")
            if c.get("is_unique"):
                flags.append("UNIQUE")
            if c.get("nullable") is False:
                flags.append("NOT NULL")
            if c.get("default_value"):
                flags.append(f"DEFAULT={c['default_value']}")
            dtype = c.get("dtype", "")
            flag_str = f", {', '.join(flags)}" if flags else ""
            desc = f" — {c['description']}" if c.get("description") else ""
            col_strs.append(f"{c['name']}({dtype}{flag_str}){desc}")

        section = f"[{table_name} 테이블]\n컬럼: {', '.join(col_strs)}"

        # FK 관계 조회
        fk_strs = await _fetch_fk_relations(client, table_name)
        if fk_strs:
            section += f"\nFK: {', '.join(fk_strs)}"

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
        "c.is_primary_key AS is_primary_key, c.is_unique AS is_unique, "
        "c.default_value AS default_value, c.description AS description\n"
        "ORDER BY c.name"
    )
    try:
        results = await client.execute_queries([query], {"name": table_name})
        return results[0] if results and results[0] else []
    except Exception as e:
        logger.debug("[TABLE_CTX] 컬럼 메타 조회 실패 (%s): %s", table_name, e)
        return []


async def _fetch_fk_relations(
    client: Neo4jClient,
    table_name: str,
) -> List[str]:
    """Neo4j에서 테이블의 FK 관계를 조회하여 문자열 리스트로 반환."""
    query = (
        "MATCH (t:Table)-[:HAS_COLUMN]->(c:Column)-[:FK_TO_COLUMN]->(tc:Column)<-[:HAS_COLUMN]-(tt:Table)\n"
        "WHERE t.name = $name OR t.fqn ENDS WITH $name\n"
        "RETURN c.name AS from_col, tt.name AS to_table, tc.name AS to_col\n"
        "ORDER BY c.name"
    )
    try:
        results = await client.execute_queries([query], {"name": table_name})
        if results and results[0]:
            return [f"{r['from_col']}→{r['to_table']}.{r['to_col']}" for r in results[0]]
        return []
    except Exception:
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
