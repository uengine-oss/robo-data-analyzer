"""방법1: 벡터 코사인 유사도 검색"""

from dataclasses import dataclass

from client.embedding_client import EmbeddingClient
from client.neo4j_client import Neo4jClient

VECTOR_INDEX_NAME = "bl_embedding_index"


@dataclass
class SearchResult:
    function_id: str
    title: str
    sequence: str
    score: float
    given: str
    when_text: str
    then_text: str


async def vector_search(
    query: str,
    client: Neo4jClient,
    embedding_client: EmbeddingClient,
    top_k: int = 10,
) -> list[SearchResult]:
    query_embedding = await embedding_client.embed_text(query)

    results = await client.execute_queries([{
        "query": f"""
            CALL db.index.vector.queryNodes('{VECTOR_INDEX_NAME}', $top_k, $query_embedding)
            YIELD node, score
            RETURN node.function_id AS function_id,
                   node.title AS title,
                   node.sequence AS sequence,
                   node.given AS given,
                   node.when AS when_text,
                   node.then AS then_text,
                   score
            ORDER BY score DESC
        """,
        "params": {"top_k": top_k, "query_embedding": query_embedding},
    }])

    rows = results[0] if results else []
    return [
        SearchResult(
            function_id=r["function_id"],
            title=r["title"],
            sequence=r.get("sequence", ""),
            score=r.get("score", 0.0),
            given=r.get("given", ""),
            when_text=r.get("when_text", ""),
            then_text=r.get("then_text", ""),
        )
        for r in rows
    ]
