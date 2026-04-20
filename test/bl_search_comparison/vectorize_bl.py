"""Rule 노드 임베딩 생성 + Neo4j 벡터 인덱스 생성 (1회 실행).

임베딩 단위는 Rule(규칙 문장). 각 Rule 에 달린 Example 의 description + narrative
(given/then 자연어 요약) 를 함께 포함해 검색 품질을 높인다.
"""

import asyncio
import json
import time

from openai import AsyncOpenAI

from client.embedding_client import EmbeddingClient
from client.neo4j_client import Neo4jClient
from config.settings import settings

VECTOR_INDEX_NAME = "rule_embedding_index"
EMBEDDING_DIM = 1536
BATCH_SIZE = 50


def _narrative(raw) -> str:
    """Example given/then 이 JSON 문자열이면 narrative 만 추출, 아니면 원문."""
    if not raw:
        return ""
    s = str(raw).strip()
    if s.startswith("{"):
        try:
            return str(json.loads(s).get("narrative") or "").strip()
        except (json.JSONDecodeError, ValueError):
            pass
    return s


def compose_rule_text(rule: dict) -> str:
    """Rule statement + 예시 description + Given/When/Then 요약을 결합해 임베딩 텍스트 생성."""
    parts = [f"규칙: {rule['statement']}"]
    for ex in (rule.get("examples") or [])[:3]:  # 대표 Example 3개
        desc = (ex.get("description") or "").strip()
        g = _narrative(ex.get("given"))
        w = (ex.get("when_") or "").strip()
        t = _narrative(ex.get("then_"))
        if desc or g or w or t:
            head = f"예시({desc})" if desc else "예시"
            parts.append(f"{head}: Given={g} / When={w} / Then={t}")
    return "\n".join(parts)


async def fetch_rules(client: Neo4jClient) -> list[dict]:
    results = await client.execute_queries([{
        "query": """
            MATCH (r:Rule)
            WHERE r.rule_embedding IS NULL
            OPTIONAL MATCH (r)-[:HAS_EXAMPLE]->(e:Example)
            WITH r, collect({description: e.description, given: e.given,
                             when_: e.when_, then_: e.then_}) AS examples
            RETURN r.rule_hash AS rule_hash,
                   r.statement AS statement,
                   examples
        """,
        "params": {},
    }])
    return results[0] if results else []


async def store_embeddings(client: Neo4jClient, rows: list[dict], embeddings: list[list[float]]):
    queries = [{
        "query": """
            MATCH (r:Rule {rule_hash: $rule_hash})
            SET r.rule_embedding = $embedding
        """,
        "params": {"rule_hash": row["rule_hash"], "embedding": emb},
    } for row, emb in zip(rows, embeddings)]
    await client.execute_queries(queries)


async def create_vector_index(client: Neo4jClient):
    await client.execute_queries([{
        "query": f"""
            CREATE VECTOR INDEX {VECTOR_INDEX_NAME} IF NOT EXISTS
            FOR (r:Rule) ON (r.rule_embedding)
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
        rows = await fetch_rules(neo4j)
        if not rows:
            print("All Rule nodes already have embeddings. Skipping.")
        else:
            print(f"Found {len(rows)} Rule nodes to vectorize")
            for i in range(0, len(rows), BATCH_SIZE):
                batch = rows[i:i + BATCH_SIZE]
                texts = [compose_rule_text(r) for r in batch]
                embeddings = await emb_client.embed_batch(texts)
                await store_embeddings(neo4j, batch, embeddings)
                print(f"  Stored embeddings for batch {i // BATCH_SIZE + 1} ({len(batch)} nodes)")

        print("Creating vector index...")
        await create_vector_index(neo4j)
        await wait_for_index(neo4j)

        results = await neo4j.execute_queries([{
            "query": "MATCH (r:Rule) WHERE r.rule_embedding IS NOT NULL RETURN count(r) AS cnt",
            "params": {},
        }])
        cnt = results[0][0]["cnt"] if results and results[0] else 0
        print(f"Verification: {cnt} Rule nodes with embeddings")

    finally:
        await neo4j.close()


if __name__ == "__main__":
    asyncio.run(run_vectorize())
