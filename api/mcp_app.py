"""MCP over HTTP — 검색 코어를 Claude Code 도구로 노출 (spec 044 FR-002).

FastMCP(streamable-http, stateless)를 FastAPI `/robo/mcp` 에 mount. 도구 = REST 와 **같은
검색 코어**(`search.retriever`) 호출 — 알고리즘 사본 0(SC-005). MCP 는 최종 결과만 반환
(진행 스트림은 프론트 REST 전용 — 사용자 합의).

spec 057: 기본 응답은 계층화 프로파일(`detail='tiered'`) + **compact 비이스케이프 JSON
텍스트** 반환 — dict 반환 시 MCP SDK 가 escape+indent 텍스트화·structuredContent 이중
전송으로 2.1배 부풀려(실측 67.3→139.9KB) CLI 한도 초과(파일 치환)를 유발했다.
계약: specs/057-cluster-result-refinement/contracts/mcp-cluster-retrieve.md

등록(아키텍트 `.mcp.json`): {"robo-cluster": {"type":"http", "url":"http://127.0.0.1:5502/robo/mcp/"}}
"""
from __future__ import annotations

import asyncio
import json
from typing import Optional

from mcp.server.fastmcp import FastMCP

from system.env_catalog import ENV
from graph.connection_context import Neo4jOverride, set_override
from graph.schema.rel_types import HAS_COLUMN

mcp = FastMCP(
    "robo-cluster",
    instructions=(
        "레거시 코드그래프 의미검색. 자연어 요구사항을 주면 관련 코드 뭉치"
        "(이름·역할·요약·읽고쓰는 테이블·호출관계·관련도)를 반환한다."
    ),
    stateless_http=True,
    streamable_http_path="/",   # mount 지점(/robo/mcp)이 곧 엔드포인트
)


def _merge_rules(rules: list) -> list:
    """동일 statement rule 병합 — examples 는 순서 보존 합집합 (spec 057 D6, 방어적)."""
    out: list[dict] = []
    by_stmt: dict[str, dict] = {}
    for r in rules or []:
        stmt = r.get("statement", "")
        cur = by_stmt.get(stmt)
        if cur is None:
            cur = {"statement": stmt, "examples": list(r.get("examples") or [])}
            by_stmt[stmt] = cur
            out.append(cur)
        else:
            cur["examples"].extend(r.get("examples") or [])
    return out


def _table_columns(table_ids: list[str]) -> dict[str, list[dict]]:
    """TABLE 컬럼 배치 조회 — 컬럼 미수집 테이블은 정상 빈 배열(조회 실패는 예외 표면화)."""
    if not table_ids:
        return {}
    from search.neo import read_driver
    drv, dbn = read_driver()
    try:
        with drv.session(database=dbn) as s:
            rows = s.run(
                f"MATCH (t:TABLE)-[:{HAS_COLUMN}]->(c:COLUMN) WHERE t.id IN $ids "
                "RETURN t.id AS tid, c.name AS name, coalesce(c.dtype,'') AS dtype, "
                "coalesce(c.is_primary_key,false) AS pk, "
                "coalesce(c.logical_name,'') AS logical_name, "
                "coalesce(c.analyzed_description, c.description, c.comment, '') AS description "
                "ORDER BY tid, c.id",
                ids=table_ids,
            ).data()
    finally:
        drv.close()
    out: dict[str, list[dict]] = {tid: [] for tid in table_ids}
    for r in rows:
        out[r["tid"]].append({
            "name": r["name"], "dtype": r["dtype"], "is_primary_key": bool(r["pk"]),
            "logical_name": r["logical_name"], "description": r["description"],
        })
    return out


_DROP_ALWAYS = ("search_text", "element_id")
_DROP_TABLE = _DROP_ALWAYS + ("stereotype", "writes", "reads", "calls")


def _tiered_clusters(clusters: list, by_id: dict) -> list:
    """LLM 소비 프로파일 (spec 057 data-model.md, 기본 detail='tiered').

    앵커 FUNCTION = rule·example 전량 + 원문 코드(사용자 결정: 유관한 것은 전문).
    evidence FUNCTION = rules 제외(rulesCount 소비 안정 위해 [] 유지) + 원문 코드 + 포함 근거.
    TABLE = 설명 + 컬럼 전량. 내부 전용 필드(search_text·element_id)는 모든 계층에서 제거.
    """
    table_ids = [n["id"] for c in clusters for n in c.get("nodes", [])
                 if n.get("label") == "TABLE"]
    columns = _table_columns(table_ids)
    tiered = []
    for c in clusters:
        nodes = []
        for n in c.get("nodes", []):
            meta = by_id.get(n.get("id"), {})
            if n.get("label") == "TABLE":
                m = {k: v for k, v in n.items() if k not in _DROP_TABLE}
                m["columns"] = columns.get(n["id"], [])
            else:
                m = {k: v for k, v in n.items() if k not in _DROP_ALWAYS}
                m["logical_name"] = meta.get("logical_name", "")
                m["code_text"] = meta.get("code_text", "")   # 원문 코드 — 빈 값은 미수집 표시
                m["rules"] = _merge_rules(m.get("rules")) if n.get("role") == "anchor" else []
            nodes.append(m)
        tiered.append({"score": c.get("score"), "nodes": nodes})
    return tiered


@mcp.tool()
async def cluster_retrieve(
    query: str, database: Optional[str] = None, detail: str = "tiered",
) -> str:
    """자연어 요구사항 → 관련 레거시 코드 뭉치(함수·규칙·영향 테이블). 응답은 JSON 텍스트.

    Args:
        query: 자연어 요구사항 (예: "주문 처리 시 재고 상태 변화").
        database: 검색할 Neo4j DB 이름 (생략 시 서버 기본 DB — 모르면 생략).
        detail: 'tiered'(기본 — 앵커는 규칙·예시·원문코드 전량, 근거 함수는 요약+원문코드,
                테이블은 컬럼 포함) | 'full'(코어 원형 전량 — 디버그·대조용).
    """
    q = (query or "").strip()
    if not q:
        return json.dumps({"error": "빈 질의"}, ensure_ascii=False)

    def _run() -> dict:
        # MCP 는 헤더 경로가 없으므로 database 인자로 요청별 DB 지정(analyze 규약과 동일 계층).
        if database:
            set_override(Neo4jOverride(
                uri=ENV.neo4j.uri, user=ENV.neo4j.user,
                password=ENV.neo4j.password, database=database))
        from search.corpus import SearchUnavailable, load_corpus
        from search.service import run_search
        try:
            clusters, meta = run_search(q)
        except SearchUnavailable as e:
            return {"error": str(e)}
        if detail == "tiered":
            clusters = _tiered_clusters(clusters, load_corpus().by_id)
        return {"clusters": clusters, "meta": meta}

    result = await asyncio.to_thread(_run)
    # compact·비이스케이프 1벌 — dict 반환 시 SDK 의 escape+indent+이중 전송이 크기를
    # 2.1배로 부풀린다(057 계약: 이 반환형을 dict 로 되돌리는 변경은 회귀).
    return json.dumps(result, ensure_ascii=False, separators=(",", ":"))
