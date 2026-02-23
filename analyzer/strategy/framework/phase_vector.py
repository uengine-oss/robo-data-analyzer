"""벡터화 Phase - Framework

Phase 2 완료 후 파일별 summary 벡터화.
"""

import time
from typing import Any

from openai import AsyncOpenAI
from httpx import Timeout

from client.neo4j_client import Neo4jClient
from client.embedding_client import EmbeddingClient
from util.text_utils import log_process


async def vectorize_summaries(
    pipeline: Any,
    client: Neo4jClient,
    api_key: str,
    file_name: str,
) -> int:
    """파일의 모든 노드 summary를 벡터화합니다.
    
    Args:
        pipeline: FrameworkPipeline 인스턴스 (공유 상태 접근용)
        client: Neo4j 클라이언트
        api_key: OpenAI API 키
        file_name: 대상 파일명
        
    Returns:
        벡터화된 노드 수
    """
    if not api_key:
        return 0
    
    timeout = Timeout(120.0, connect=120.0, read=300.0)
    openai_client = AsyncOpenAI(
        api_key=api_key,
        timeout=timeout,
        max_retries=3
    )
    embedding_client = EmbeddingClient(openai_client)
    
    query = """
    MATCH (__cy_n__)
    WHERE (__cy_n__:METHOD OR __cy_n__:CONSTRUCTOR OR __cy_n__:FIELD)
      AND __cy_n__.file_name = $file_name
      AND __cy_n__.summary IS NOT NULL
      AND __cy_n__.summary <> ''
      AND (__cy_n__.summary_vec IS NULL OR size(__cy_n__.summary_vec) = 0)
    RETURN elementId(__cy_n__) AS nid, 
           __cy_n__.summary AS summary,
           labels(__cy_n__)[0] AS node_type
    """
    
    async with pipeline._cypher_lock:
        result = await client.execute_with_params(query, {"file_name": file_name})
    
    nodes = result if result else []
    if not nodes:
        return 0
    
    batch_size = 30
    vectorized_count = 0
    
    for i in range(0, len(nodes), batch_size):
        batch = nodes[i:i + batch_size]
        texts = [item.get("summary", "") for item in batch]
        
        embed_start = time.time()
        vectors = await embedding_client.embed_batch(texts)
        embed_time = time.time() - embed_start
        
        log_process("VECTORIZE", "SUMMARY", f"{file_name}: {len(vectors)}개 노드 임베딩 완료 ({embed_time:.2f}초)")
        
        if not vectors:
            continue
            
        vector_updates = []
        for item, vector in zip(batch, vectors):
            if vector:
                vector_updates.append({
                    "nid": item['nid'],
                    "vector": vector
                })
                vectorized_count += 1
        
        if vector_updates:
            update_query = """
            UNWIND $items AS item
            MATCH (__cy_n__) WHERE elementId(__cy_n__) = item.nid
            SET __cy_n__.summary_vec = item.vector
            RETURN __cy_n__
            """
            async with pipeline._cypher_lock:
                await client.execute_with_params(update_query, {"items": vector_updates})
    
    return vectorized_count
