"""순수 벡터 검색 vs DeepAgent 시맨틱 검색 비교

각 질의에 대해 양쪽 결과를 나란히 출력한다.
"""

import asyncio
import sys
import time

from openai import AsyncOpenAI

from client.embedding_client import EmbeddingClient
from client.neo4j_client import Neo4jClient
from config.settings import settings

from test.bl_search_comparison.vector_search import vector_search
from test.bl_search_comparison.semantic_search import semantic_search
from test.bl_search_comparison.vectorize_bl import VECTOR_INDEX_NAME


QUERIES = [
    "카드 비밀번호가 입력되면 어떤 처리를 하나?",
    "은행 예금주명이 비어있으면 어떻게 처리하나?",
    "op_cl이 '03'일 때 EDI 인증이력 저장은?",
    "공통코드에서 결과코드가 정상전화여부 코드군에 속하는지 확인하는 로직은?",
    "SKB 은행에서 결과코드 'A002'일 때 오류 메시지는?",
    "간편결제사가 등록되지 않았으면 어떻게 처리하나?",
    "외부인증 결과코드 '0000'일 때 반영여부 판단은?",
    "인증이력 후처리 시 회사구분코드가 'B'이면 예금주명은?",
]


def _fn(function_id: str) -> str:
    return function_id.split(".")[-1] if function_id else "??"


async def run_single_query(
    query: str,
    neo4j_client: Neo4jClient,
    emb_client: EmbeddingClient,
):
    sep = "=" * 90

    print(f"\n{sep}")
    print(f"질의: {query}")
    print(sep)

    # --- 순수 벡터 검색 ---
    t0 = time.perf_counter()
    vec_results = await vector_search(query, neo4j_client, emb_client, top_k=10)
    vec_time = time.perf_counter() - t0

    print(f"\n[순수 벡터 top-10]  ({vec_time:.2f}s)")
    for i, r in enumerate(vec_results):
        print(f"  #{i+1:>2} [{r.score:.3f}] {_fn(r.function_id)}: {r.title}")

    # --- DeepAgent 시맨틱 검색 ---
    t0 = time.perf_counter()
    sem_result = await semantic_search(query, api_key=settings.llm.api_key)
    sem_time = time.perf_counter() - t0

    print(f"\n[DeepAgent 시맨틱 검색]  ({sem_time:.2f}s / {sem_result.total_tokens:,} tokens)")

    # 도구 호출 이력
    if sem_result.tool_calls:
        print(f"\n  도구 호출 이력 ({len(sem_result.tool_calls)}회):")
        for i, tc in enumerate(sem_result.tool_calls):
            args_short = tc.args[:80] + "..." if len(tc.args) > 80 else tc.args
            print(f"    [{i+1}] {tc.tool_name}({args_short})")
            if tc.result_preview:
                preview = tc.result_preview[:100] + "..." if len(tc.result_preview) > 100 else tc.result_preview
                print(f"        → {preview}")

    # 최종 선택 결과
    print()
    if sem_result.selected:
        for item in sem_result.selected:
            fid = item.get("function_id", "")
            title = item.get("title", "")
            conf = item.get("confidence", "?")
            reason = item.get("reason", "")
            print(f"  ✅ [{conf}] {_fn(fid)}: {title}")
            print(f"     → {reason}")
    else:
        print("  (관련 BL 없음)")

    print(sep)


async def main():
    sys.stdout.reconfigure(encoding="utf-8")
    api_key = settings.llm.api_key
    if not api_key:
        print("ERROR: LLM_API_KEY not set")
        return

    neo4j_client = Neo4jClient(database=settings.neo4j.database)
    openai_client = AsyncOpenAI(api_key=api_key)
    emb_client = EmbeddingClient(openai_client)

    try:
        results = await neo4j_client.execute_queries([{
            "query": "SHOW INDEXES YIELD name WHERE name = $name RETURN name",
            "params": {"name": VECTOR_INDEX_NAME},
        }])
        if not (results[0] if results else []):
            print("ERROR: Vector index not found. Run vectorize_bl.py first.")
            return

        print("=" * 90)
        print("BL 검색 비교: 순수 벡터 vs DeepAgent 시맨틱 검색")
        print(f"벡터: top-10 / DeepAgent: 도구 5개 + 5단계 파이프라인 (model: {settings.llm.model})")
        print("=" * 90)

        for query in QUERIES:
            await run_single_query(query, neo4j_client, emb_client)

    finally:
        await neo4j_client.close()


if __name__ == "__main__":
    asyncio.run(main())
