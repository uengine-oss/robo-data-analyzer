"""MCP over HTTP — 검색 코어를 Claude Code 도구로 노출 (spec 044 FR-002).

FastMCP(streamable-http, stateless)를 FastAPI `/robo/mcp` 에 mount. 도구 = REST 와 **같은
검색 코어**(`search.retriever`) 호출 — 알고리즘 사본 0(SC-005). MCP 는 최종 결과만 반환
(진행 스트림은 프론트 REST 전용 — 사용자 합의).

등록(아키텍트 `.mcp.json`): {"robo-cluster": {"type":"http", "url":"http://127.0.0.1:5502/robo/mcp/"}}
"""
from __future__ import annotations

import asyncio
from typing import Optional

from mcp.server.fastmcp import FastMCP

from shared.config.env_catalog import ENV
from shared.neo4j.connection_context import Neo4jOverride, set_override

mcp = FastMCP(
    "robo-cluster",
    instructions=(
        "레거시 코드그래프 의미검색. 자연어 요구사항을 주면 관련 코드 뭉치"
        "(이름·역할·요약·읽고쓰는 테이블·호출관계·관련도)를 반환한다."
    ),
    stateless_http=True,
    streamable_http_path="/",   # mount 지점(/robo/mcp)이 곧 엔드포인트
)


@mcp.tool()
async def cluster_retrieve(query: str, database: Optional[str] = None) -> dict:
    """자연어 요구사항 → 관련 레거시 코드 뭉치.

    Args:
        query: 자연어 요구사항 (예: "주문 처리 시 재고 상태 변화").
        database: 검색할 Neo4j DB 이름 (생략 시 서버 기본 DB).
    """
    q = (query or "").strip()
    if not q:
        return {"error": "빈 질의"}

    def _run() -> dict:
        # MCP 는 헤더 경로가 없으므로 database 인자로 요청별 DB 지정(analyze 규약과 동일 계층).
        if database:
            set_override(Neo4jOverride(
                uri=ENV.neo4j.uri, user=ENV.neo4j.user,
                password=ENV.neo4j.password, database=database))
        from search.corpus import SearchUnavailable
        from search.service import run_search
        try:
            clusters, meta = run_search(q)
        except SearchUnavailable as e:
            return {"error": str(e)}
        return {"clusters": clusters, "meta": meta}

    return await asyncio.to_thread(_run)
