"""클라이언트 모듈

외부 서비스 클라이언트를 제공합니다.
- llm_client: LLM API 클라이언트 (OpenAI, Custom)
- embedding_client: 임베딩 API 클라이언트
- neo4j_client: Neo4j 데이터베이스 클라이언트

사용법:
    from client.llm_client import get_llm
    from client.embedding_client import EmbeddingClient
    from client.neo4j_client import Neo4jClient
"""

__all__ = ["get_llm", "EmbeddingClient", "Neo4jClient"]


def __getattr__(name: str):
    """Lazy import to avoid circular dependencies"""
    if name == "get_llm":
        from client.llm_client import get_llm
        return get_llm
    elif name == "EmbeddingClient":
        from client.embedding_client import EmbeddingClient
        return EmbeddingClient
    elif name == "Neo4jClient":
        from client.neo4j_client import Neo4jClient
        return Neo4jClient
    raise AttributeError(f"module 'client' has no attribute '{name}'")

