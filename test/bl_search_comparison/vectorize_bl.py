"""BL 노드 임베딩 생성 + Neo4j 벡터 인덱스 생성 (1회 실행)"""

import asyncio
import time

from openai import AsyncOpenAI

from client.embedding_client import EmbeddingClient
from client.neo4j_client import Neo4jClient
from config.settings import settings

VECTOR_INDEX_NAME = "bl_embedding_index"
EMBEDDING_DIM = 1536
BATCH_SIZE = 50


def compose_bl_text(bl: dict) -> str:
    parts = []
    if bl.get("title"):
        parts.append(f"제목: {bl['title']}")
    if bl.get("given"):
        parts.append(f"사전조건: {bl['given']}")
    if bl.get("bwhen"):
        parts.append(f"조건: {bl['bwhen']}")
    if bl.get("bthen"):
        parts.append(f"결과: {bl['bthen']}")
    return "\n".join(parts)


async def fetch_bl_nodes(client: Neo4jClient) -> list[dict]:
    results = await client.execute_queries([{
        "query": """
            MATCH (bl:BusinessLogic)
            WHERE bl.bl_embedding IS NULL
            RETURN bl.function_id AS function_id,
                   bl.title AS title,
                   bl.given AS given,
                   bl.when AS bwhen,
                   bl.then AS bthen
        """,
        "params": {},
    }])
    return results[0] if results else []


async def store_embeddings(client: Neo4jClient, rows: list[dict], embeddings: list[list[float]]):
    queries = []
    for row, emb in zip(rows, embeddings):
        queries.append({
            "query": """
                MATCH (bl:BusinessLogic {function_id: $fid, title: $title})
                SET bl.bl_embedding = $embedding
            """,
            "params": {
                "fid": row["function_id"],
                "title": row["title"],
                "embedding": emb,
            },
        })
    await client.execute_queries(queries)


async def create_vector_index(client: Neo4jClient):
    await client.execute_queries([{
        "query": f"""
            CREATE VECTOR INDEX {VECTOR_INDEX_NAME} IF NOT EXISTS
            FOR (bl:BusinessLogic) ON (bl.bl_embedding)
            OPTIONS {{indexConfig: {{
                `vector.dimensions`: {EMBEDDING_DIM},
                `vector.similarity_function`: 'cosine'
            }}}}
        """,
        "params": {},
    }])


async def wait_for_index(client: Neo4jClient, timeout: int = 60):
    start = time.time()
    while time.time() - start < timeout:
        results = await client.execute_queries([{
            "query": "SHOW INDEXES YIELD name, state WHERE name = $name RETURN state",
            "params": {"name": VECTOR_INDEX_NAME},
        }])
        rows = results[0] if results else []
        if rows and rows[0].get("state") == "ONLINE":
            print(f"  Vector index '{VECTOR_INDEX_NAME}' is ONLINE")
            return
        await asyncio.sleep(1)
    raise TimeoutError(f"Vector index did not come ONLINE within {timeout}s")


async def run_vectorize():
    neo4j = Neo4jClient(database=settings.neo4j.database)
    openai_client = AsyncOpenAI(api_key=settings.llm.api_key)
    emb_client = EmbeddingClient(openai_client)

    try:
        # 1. Fetch BL nodes without embeddings
        rows = await fetch_bl_nodes(neo4j)
        if not rows:
            print("All BL nodes already have embeddings. Skipping.")
        else:
            print(f"Found {len(rows)} BL nodes to vectorize")

            # 2. Generate embeddings in batches
            for i in range(0, len(rows), BATCH_SIZE):
                batch = rows[i:i + BATCH_SIZE]
                texts = [compose_bl_text(r) for r in batch]
                embeddings = await emb_client.embed_batch(texts)
                await store_embeddings(neo4j, batch, embeddings)
                print(f"  Stored embeddings for batch {i // BATCH_SIZE + 1} ({len(batch)} nodes)")

        # 3. Create vector index
        print("Creating vector index...")
        await create_vector_index(neo4j)
        await wait_for_index(neo4j)

        # 4. Verify
        results = await neo4j.execute_queries([{
            "query": "MATCH (bl:BusinessLogic) WHERE bl.bl_embedding IS NOT NULL RETURN count(bl) AS cnt",
            "params": {},
        }])
        cnt = results[0][0]["cnt"] if results and results[0] else 0
        print(f"Verification: {cnt} BL nodes with embeddings")

    finally:
        await neo4j.close()


if __name__ == "__main__":
    asyncio.run(run_vectorize())
