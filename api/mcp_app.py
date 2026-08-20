"""MCP over HTTP — 레거시 검색 목록과 선택 노드 상세 조회를 노출한다.

FastMCP(streamable-http, stateless)를 FastAPI `/robo/mcp` 에 mount. 도구 = REST 와 **같은
검색 코어**(`search.ranking.retriever`) 호출 — 알고리즘 사본 0(SC-005). MCP 는 최종 결과만 반환
(진행 스트림은 프론트 REST 전용 — 사용자 합의).

spec 060/131: ``cluster_retrieve``는 선택용 목록만, ``node_detail`` 기본 보기는 선택한
ID의 검증된 semantic frame과 ID-deduplicated linked context를 반환한다. 원문은 감사용
``full`` 보기에서만 명시적으로 조회한다. 두 도구 모두 **compact 비이스케이프 JSON 텍스트**
한 벌을 반환한다.
dict 반환 시 MCP SDK가 escape+indent 텍스트화·structuredContent 이중 전송으로 2.1배
부풀리므로 문자열 반환은 전송 계약이다.

등록(아키텍트 `.mcp.json`): {"robo-cluster": {"type":"http", "url":"http://127.0.0.1:15502/robo/mcp/"}}
"""
from __future__ import annotations

import asyncio
import json
from typing import Optional

from mcp.server.fastmcp import FastMCP

from shared.config.settings import SETTINGS
from graph.connection_context import Neo4jOverride, set_override
from search.corpus.evidence_reader import compact_rules

mcp = FastMCP(
    "robo-cluster",
    instructions=(
        "레거시 코드그래프 의미검색. cluster_retrieve로 관련 노드 목록과 규칙 근거를 먼저 "
        "찾고, 필요한 ID만 node_detail의 frame 보기로 semantic frame과 관계 근거를 조회한다. "
        "정상 생성에서 full 원문 보기를 사용하지 않는다."
    ),
    stateless_http=True,
    streamable_http_path="/",   # mount 지점(/robo/mcp)이 곧 엔드포인트
)


_DROP_ALWAYS = ("element_id",)
_DROP_TABLE = _DROP_ALWAYS + ("stereotype", "writes", "reads", "calls")


def _list_clusters(clusters: list, by_id: dict) -> list:
    """선택용 목록 프로파일 — ID·요약·관계단서·앵커 규칙만, 상세 원문/컬럼은 제외."""
    listed = []
    for c in clusters:
        nodes = []
        for n in c.get("nodes", []):
            meta = by_id.get(n.get("id"), {})
            if n.get("label") == "TABLE":
                m = {k: v for k, v in n.items() if k not in _DROP_TABLE}
            else:
                m = {k: v for k, v in n.items() if k not in _DROP_ALWAYS}
                m["logical_name"] = meta.get("logical_name", "")
                m["rules"] = compact_rules(m.get("rules")) if n.get("role") == "anchor" else []
            nodes.append(m)
        listed.append({"score": c.get("score"), "nodes": nodes})
    return listed


def _select_database(database: Optional[str]) -> None:
    """요청 DB를 고르되 미지정 호출이 직전 요청의 override를 상속하지 않게 한다."""
    selected = (database or SETTINGS.graph_database.database).strip()
    set_override(Neo4jOverride(
        uri=SETTINGS.graph_database.uri,
        user=SETTINGS.graph_database.user,
        password=SETTINGS.graph_database.password,
        database=selected,
    ))


def _json(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


@mcp.tool()
async def cluster_retrieve(
    query: str, database: Optional[str] = None,
) -> str:
    """자연어 요구사항 → 관련 레거시 노드 목록·규칙 근거. 응답은 JSON 텍스트.

    Args:
        query: 자연어 요구사항 (예: "주문 처리 시 재고 상태 변화").
        database: 운영자가 지정한 Neo4j DB 이름. 프로젝트/코퍼스 이름을 추측해 넣지 말고,
            지정받지 않았으면 생략한다(서버 기본 DB 사용).
    """
    q = (query or "").strip()
    if not q:
        return _json({"error": {"code": "EMPTY_QUERY", "message": "query is required"}})

    def _run() -> dict:
        # MCP 는 헤더 경로가 없으므로 database 인자로 요청별 DB 지정(analyze 규약과 동일 계층).
        _select_database(database)
        from search.corpus.corpus import SearchUnavailable, load_corpus
        from search.service import run_search
        try:
            clusters, meta = run_search(q)
            corpus = load_corpus(generation_id=meta.get("generation_id"))
        except SearchUnavailable as e:
            return {"error": {"code": "SEARCH_UNAVAILABLE", "message": str(e)}}
        return {"clusters": _list_clusters(clusters, corpus.by_id), "meta": meta}

    result = await asyncio.to_thread(_run)
    # compact·비이스케이프 1벌 — dict 반환 시 SDK 의 escape+indent+이중 전송이 크기를
    # 2.1배로 부풀린다(057 계약: 이 반환형을 dict 로 되돌리는 변경은 회귀).
    return _json(result)


@mcp.tool()
async def node_detail(
    node_id: str, database: Optional[str] = None, view: str = "frame",
) -> str:
    """선택한 노드의 검증된 semantic frame과 연결 근거를 조회한다.

    Args:
        node_id: ``cluster_retrieve`` 목록에서 선택한 노드 ID.
        database: 검색과 같은 운영자 지정 Neo4j DB 이름. 프로젝트/코퍼스 이름을 추측해
            넣지 말고, 지정받지 않았으면 생략한다(서버 기본 DB 사용).
        view: ``frame``은 Architect 정상 생성용 source-free 의미 프레임 보기다.
            ``full``은 사람의 감사·결함 추적용 원문 상세다. ``gwt``는 ``frame``의 폐기 전 별칭이다.
    """
    requested_id = (node_id or "").strip()
    if not requested_id:
        return _json({"error": {"code": "EMPTY_ID", "message": "node_id is required"}})
    requested_view = (view or "").strip().lower()
    if requested_view not in {"full", "frame", "gwt"}:
        return _json({"error": {"code": "INVALID_VIEW", "message": "view must be frame or full"}})

    def _run() -> dict:
        _select_database(database)
        from search.corpus.node_detail import read_node_detail
        return read_node_detail(
            requested_id,
            view="frame" if requested_view == "gwt" else requested_view,
        )

    return _json(await asyncio.to_thread(_run))
