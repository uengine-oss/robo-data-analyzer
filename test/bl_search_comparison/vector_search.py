"""Rule 벡터 코사인 유사도 검색 (Example Mapping 전환 후 기본 검색 방식).

Rule.statement + 대표 Example 을 결합한 임베딩으로 의미 유사도를 계산.
"""

from dataclasses import dataclass
from typing import Any

from client.embedding_client import EmbeddingClient
from client.neo4j_client import Neo4jClient

VECTOR_INDEX_NAME = "rule_embedding_index"


@dataclass
class SearchResult:
    rule_hash: str
    statement: str
    function_id: str
    local_id: str
    score: float
    examples: list[dict[str, Any]]  # [{description, given, when_, then_, is_boundary}, ...]


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
            YIELD node AS r, score
            OPTIONAL MATCH (f:FUNCTION)-[rel:HAS_RULE]->(r)
            OPTIONAL MATCH (r)-[:HAS_EXAMPLE]->(e:Example)
            WITH r, score,
                 collect(DISTINCT f.function_id)[0] AS function_id,
                 collect(DISTINCT rel.local_id)[0] AS local_id,
                 collect(DISTINCT {{description: e.description, given: e.given, when_: e.when_,
                                    then_: e.then_, is_boundary: e.is_boundary}}) AS examples
            RETURN r.rule_hash AS rule_hash,
                   r.statement AS statement,
                   function_id,
                   local_id,
                   score,
                   examples
            ORDER BY score DESC
        """,
        "params": {"top_k": top_k, "query_embedding": query_embedding},
    }])

    rows = results[0] if results else []
    return [
        SearchResult(
            rule_hash=r["rule_hash"],
            statement=r.get("statement", ""),
            function_id=r.get("function_id", ""),
            local_id=r.get("local_id", ""),
            score=r.get("score", 0.0),
            examples=[e for e in r.get("examples", []) if e and e.get("given")],
        )
        for r in rows
    ]
