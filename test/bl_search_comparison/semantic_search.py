"""DeepAgent 기반 BL 시맨틱 검색

5단계 파이프라인:
  1. 질의 분석 & HyDE 가설
  2. 다중 전략 검색 (벡터 + 키워드 + HyDE)
  3. 후보 검증 (GWT 전문 + 3요소 매칭)
  4. 결과 불충분 시 재시도 (step-back / decompose)
  5. 최종 제출
"""

import asyncio
import json
import threading
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from langchain_core.tools import tool
from neo4j import GraphDatabase
from openai import OpenAI

from client.llm_client import get_llm
from config.settings import settings

VECTOR_INDEX_NAME = "bl_embedding_index"


@dataclass
class ToolCallLog:
    tool_name: str
    args: str
    result_preview: str  # 결과 앞 150자


@dataclass
class SemanticSearchResult:
    selected: list[dict] = field(default_factory=list)  # [{function_id, title, confidence, reason}]
    tool_calls: list[ToolCallLog] = field(default_factory=list)
    total_tokens: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0


def _load_system_prompt() -> str:
    yaml_path = Path(__file__).parent / "bl_semantic_search.yaml"
    data = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    return data["system_prompt"]


def _neo4j_query(driver, query: str, params: dict | None = None) -> list[dict]:
    with driver.session(database=settings.neo4j.database) as session:
        result = session.run(query, params or {})
        return [dict(record) for record in result]


def _embed_sync(sync_openai: OpenAI, text: str) -> list[float]:
    resp = sync_openai.embeddings.create(
        model=settings.llm.embedding_model,
        input=text,
        encoding_format="float",
    )
    return resp.data[0].embedding


def build_bl_search_tools(
    driver,
    sync_openai: OpenAI,
    collector: list[dict],
    lock: threading.Lock,
):
    @tool
    def bl_vector_search(query: str, top_k: int = 15) -> str:
        """벡터 유사도로 BL을 검색합니다. 제목과 GWT 미리보기를 반환합니다.
        원본 질의, HyDE 가설, step-back 질의 등 다양한 텍스트로 호출 가능합니다."""
        embedding = _embed_sync(sync_openai, query)
        rows = _neo4j_query(driver, f"""
            CALL db.index.vector.queryNodes('{VECTOR_INDEX_NAME}', $top_k, $embedding)
            YIELD node, score
            RETURN node.function_id AS function_id,
                   node.title AS title,
                   node.sequence AS sequence,
                   score,
                   substring(coalesce(node.given, ''), 0, 100) AS given_preview,
                   substring(coalesce(node.when, ''), 0, 100) AS when_preview,
                   substring(coalesce(node.then, ''), 0, 100) AS then_preview
            ORDER BY score DESC
        """, {"top_k": top_k, "embedding": embedding})
        return json.dumps(rows, ensure_ascii=False)

    @tool
    def bl_keyword_search(keyword: str) -> str:
        """BL의 GWT 텍스트에서 정확한 문자열을 검색합니다.
        코드값('A002'), 테이블명('zpay_ap_rltm_auth_hst'), 코드군ID('ZPAY_C_RARNC') 등에 사용하세요."""
        rows = _neo4j_query(driver, """
            MATCH (bl:BusinessLogic)
            WHERE bl.given CONTAINS $keyword
               OR bl.when CONTAINS $keyword
               OR bl.then CONTAINS $keyword
               OR bl.title CONTAINS $keyword
            RETURN bl.function_id AS function_id,
                   bl.title AS title,
                   bl.sequence AS sequence,
                   CASE WHEN bl.title CONTAINS $keyword THEN 'title'
                        WHEN bl.given CONTAINS $keyword THEN 'given'
                        WHEN bl.when CONTAINS $keyword THEN 'when'
                        ELSE 'then' END AS matched_field,
                   CASE WHEN bl.title CONTAINS $keyword THEN substring(bl.title, 0, 150)
                        WHEN bl.given CONTAINS $keyword THEN substring(bl.given, 0, 150)
                        WHEN bl.when CONTAINS $keyword THEN substring(bl.when, 0, 150)
                        ELSE substring(bl.then, 0, 150) END AS snippet
        """, {"keyword": keyword})
        return json.dumps(rows, ensure_ascii=False)

    @tool
    def get_bl_detail(function_id: str, title: str) -> str:
        """특정 BL의 GWT 전문과 소속 함수명을 반환합니다.
        유력 후보의 3요소 매칭 판단을 위해 반드시 전문을 읽으세요."""
        rows = _neo4j_query(driver, """
            MATCH (bl:BusinessLogic {function_id: $fid, title: $title})
            OPTIONAL MATCH (f:FUNCTION {function_id: $fid})
            RETURN bl.function_id AS function_id,
                   bl.title AS title,
                   bl.given AS given,
                   bl.when AS when_text,
                   bl.then AS then_text,
                   f.name AS function_name
        """, {"fid": function_id, "title": title})
        return json.dumps(rows[0] if rows else {}, ensure_ascii=False)

    @tool
    def get_function_context(function_id: str) -> str:
        """BL이 속한 함수의 전체 맥락을 반환합니다.
        summary, 호출관계(CALLS/CALLED_BY), 테이블 접근(READS/WRITES), 같은 함수의 BL 목록."""
        # 함수 기본 정보
        func_rows = _neo4j_query(driver, """
            MATCH (f:FUNCTION {function_id: $fid})
            RETURN f.name AS name, f.summary AS summary, f.stereotype AS stereotype
        """, {"fid": function_id})
        func_info = func_rows[0] if func_rows else {}

        # 호출 관계
        call_rows = _neo4j_query(driver, """
            OPTIONAL MATCH (f:FUNCTION {function_id: $fid})-[:CALLS]->(callee:FUNCTION)
            WITH collect(callee.name) AS calls_list
            OPTIONAL MATCH (caller:FUNCTION)-[:CALLS]->(f2:FUNCTION {function_id: $fid})
            RETURN calls_list AS calls, collect(caller.name) AS called_by
        """, {"fid": function_id})
        calls = call_rows[0] if call_rows else {"calls": [], "called_by": []}

        # 테이블 접근
        table_rows = _neo4j_query(driver, """
            MATCH (f:FUNCTION {function_id: $fid})-[r:READS|WRITES]->(t:Table)
            RETURN t.name AS table_name, type(r) AS access
        """, {"fid": function_id})

        # 같은 함수의 BL 목록
        bl_rows = _neo4j_query(driver, """
            MATCH (f:FUNCTION {function_id: $fid})-[:HAS_BUSINESS_LOGIC]->(bl:BusinessLogic)
            RETURN bl.title AS title, bl.sequence AS sequence
            ORDER BY bl.sequence
        """, {"fid": function_id})

        result = {
            "name": func_info.get("name", ""),
            "summary": func_info.get("summary", ""),
            "tables": [f"{r['table_name']}({r['access']})" for r in table_rows],
            "bl_list": [r["title"] for r in bl_rows],
        }
        return json.dumps(result, ensure_ascii=False)

    @tool
    def select_relevant_bls(results_json: str) -> str:
        """최종 결과를 제출합니다. 반드시 이 도구를 호출하여 검색을 종료하세요.
        results_json: '[{"function_id":"...","title":"...","confidence":"EXACT","reason":"..."}]'
        빈 배열 '[]'도 유효합니다."""
        try:
            items = json.loads(results_json)
        except json.JSONDecodeError:
            return "JSON 파싱 오류. 올바른 JSON 배열을 전달하세요."
        with lock:
            collector.extend(items)
        return f"제출 완료: {len(items)}개 BL"

    return [bl_vector_search, bl_keyword_search, get_bl_detail, get_function_context, select_relevant_bls]


async def semantic_search(query: str, api_key: str = "") -> SemanticSearchResult:
    """DeepAgent 기반 BL 시맨틱 검색 실행."""
    from deepagents import create_deep_agent

    api_key = api_key or settings.llm.api_key
    llm = get_llm(api_key=api_key)
    system_prompt = _load_system_prompt()

    driver = GraphDatabase.driver(
        settings.neo4j.uri,
        auth=(settings.neo4j.user, settings.neo4j.password),
    )
    sync_openai = OpenAI(api_key=api_key)

    collector: list[dict] = []
    lock = threading.Lock()
    tools = build_bl_search_tools(driver, sync_openai, collector, lock)

    agent = create_deep_agent(model=llm, tools=tools, system_prompt=system_prompt)

    try:
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None,
            lambda: agent.invoke({"messages": [{"role": "user", "content": query}]}),
        )
    finally:
        driver.close()

    # Extract token usage + tool call history
    total_tokens = 0
    prompt_tokens = 0
    completion_tokens = 0
    tool_calls: list[ToolCallLog] = []

    for msg in result.get("messages", []):
        usage = getattr(msg, "usage_metadata", None)
        if usage:
            total_tokens += usage.get("total_tokens", 0)
            prompt_tokens += usage.get("input_tokens", 0)
            completion_tokens += usage.get("output_tokens", 0)

        # AI message의 tool_calls 추출
        ai_tool_calls = getattr(msg, "tool_calls", None)
        if ai_tool_calls:
            for tc in ai_tool_calls:
                tool_calls.append(ToolCallLog(
                    tool_name=tc.get("name", ""),
                    args=json.dumps(tc.get("args", {}), ensure_ascii=False)[:200],
                    result_preview="",
                ))

        # ToolMessage의 content (도구 반환값) 추출
        msg_type = getattr(msg, "type", "")
        if msg_type == "tool":
            tool_name = getattr(msg, "name", "")
            content = getattr(msg, "content", "")
            # 마지막으로 추가된 같은 이름의 tool call에 결과 연결
            for tc_log in reversed(tool_calls):
                if tc_log.tool_name == tool_name and not tc_log.result_preview:
                    tc_log.result_preview = (content or "")[:150]
                    break

    return SemanticSearchResult(
        selected=collector,
        tool_calls=tool_calls,
        total_tokens=total_tokens,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
    )
