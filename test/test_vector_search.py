"""자연어 질의 → 벡터 검색 간단 테스트

자연어 질의를 1536차원 벡터로 임베딩하고 Neo4j에서 유사 노드 검색
"""

import asyncio
import sys
from pathlib import Path
from typing import List, Dict, Any

# 프로젝트 루트를 sys.path에 추가
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))


async def search_by_query(query: str, top_k: int = 10) -> List[Dict[str, Any]]:
    """자연어 질의로 벡터 검색
    
    Args:
        query: 자연어 질의 (예: "주문 취소 처리하는 프로시저")
        top_k: 결과 개수
    
    Returns:
        검색 결과 리스트
    """
    # 순환 import 방지를 위해 직접 드라이버 사용
    from neo4j import AsyncGraphDatabase
    from openai import AsyncOpenAI
    from client.embedding_client import EmbeddingClient
    from config.settings import settings
    
    print(f"\n{'='*100}")
    print(f"🔍 질의: {query}")
    print(f"{'='*100}\n")
    
    # 1. 질의를 1536차원 벡터로 임베딩
    print("📊 임베딩 중...")
    openai_client = AsyncOpenAI(api_key=settings.openai_api_key)
    embedding_client = EmbeddingClient(client=openai_client)
    embedding_vector = await embedding_client.embed_text(query)
    print(f"   ✅ 완료 (차원: {len(embedding_vector)})\n")
    
    # 2. Neo4j 벡터 검색
    print("🔎 Neo4j 검색 중...")
    
    driver = AsyncGraphDatabase.driver(
        settings.neo4j.uri,
        auth=(settings.neo4j.user, settings.neo4j.password)
    )
    
    try:
        async with driver.session(database=settings.neo4j.database) as session:
            result = await session.run(
                """
                CALL db.index.vector.queryNodes(
                    "method_vec_idx",
                    $top_k,
                    $embedding
                )
                YIELD node, score
                RETURN node, score
                ORDER BY score DESC
                """,
                top_k=top_k,
                embedding=embedding_vector
            )
            
            records = await result.data()
            print(f"   ✅ {len(records)}건 발견\n")
            return records
    finally:
        await driver.close()


def print_results(results: List[Dict[str, Any]]):
    """결과를 예쁘게 출력"""
    if not results:
        print("❌ 결과 없음\n")
        return
    
    print(f"{'='*100}")
    print(f"📋 검색 결과: {len(results)}건")
    print(f"{'='*100}\n")
    
    for idx, record in enumerate(results, 1):
        node = record.get("node", {})
        score = record.get("score", 0.0)
        
        # 타입
        labels = node.get("labels", [])
        node_type = labels[0] if labels else "Unknown"
        
        # 헤더
        print(f"┌─ [{idx}] {'🟢' if score > 0.85 else '🟡' if score > 0.75 else '🟠'} 유사도: {score:.4f} │ {node_type}")
        print("│")
        
        # 이름/프로시저
        if "procedure_name" in node:
            proc_name = node.get("procedure_name", "")
            schema = node.get("schema_name", "")
            if schema:
                print(f"│  📦 {schema}.{proc_name}")
            else:
                print(f"│  📦 {proc_name}")
        elif "name" in node:
            name = node.get("name", "")
            pkg = node.get("package_name", "")
            if pkg:
                print(f"│  📦 {pkg}.{name}")
            else:
                print(f"│  📦 {name}")
        
        # 파일 위치
        file_name = node.get("file_name", "")
        directory = node.get("directory", "")
        start_line = node.get("startLine", "")
        end_line = node.get("endLine", "")
        
        if file_name:
            location = f"{directory}/{file_name}" if directory else file_name
            if start_line:
                location += f" (L{start_line}-{end_line})"
            print(f"│  📂 {location}")
        
        # 요약
        summary = node.get("summary", "")
        if summary:
            # 첫 150자 + 줄바꿈 처리
            lines = summary.replace('\n', ' ').strip()
            if len(lines) > 150:
                lines = lines[:150] + "..."
            print(f"│")
            print(f"│  💬 {lines}")
        
        # 시그니처
        signature = node.get("signature", "")
        if signature:
            sig_short = signature[:100] + "..." if len(signature) > 100 else signature
            print(f"│  ⚡ {sig_short}")
        
        print("└" + "─" * 98)
        print()


async def main():
    """메인"""
    
    # CLI 인자로 질의가 들어오면 그것만 실행
    if len(sys.argv) > 1:
        query = " ".join(sys.argv[1:])
        print("\n" + "🔍 " * 30)
        print(f"질의: {query}")
        print("🔍 " * 30 + "\n")
        
        results = await search_by_query(query, top_k=10)
        print_results(results)
        return
    
    # 인자 없으면 테스트 질의 실행
    query = "자금시장과 관련한 계정원장을 신규 개설하는 기능"
    
    print("\n" + "🔍 " * 30)
    print("Neo4j 벡터 검색 테스트")
    print("🔍 " * 30 + "\n")
    
    results = await search_by_query(query, top_k=5)
    print_results(results)


if __name__ == "__main__":
    asyncio.run(main())

