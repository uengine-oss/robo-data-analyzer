"""ROBO Analyzer 분석 모듈

소스 코드를 분석하여 Neo4j 그래프로 변환하는 핵심 모듈입니다.

주요 구성:
- strategy: 분석 전략 패턴 (Framework, DBMS)

Neo4jClient는 client 모듈에서 import하세요:
    from client.neo4j_client import Neo4jClient
"""

__all__ = [
    "AnalyzerFactory",
    "AnalyzerStrategy",
]


def __getattr__(name: str):
    """Lazy import로 순환 참조 방지
    
    client.neo4j_client → analyzer.pipeline_control 경로에서
    analyzer/__init__.py가 실행될 때 .strategy를 즉시 로드하면
    base_analyzer → client.neo4j_client 순환 참조가 발생함.
    """
    if name == "AnalyzerFactory":
        from .strategy import AnalyzerFactory
        return AnalyzerFactory
    elif name == "AnalyzerStrategy":
        from .strategy import AnalyzerStrategy
        return AnalyzerStrategy
    raise AttributeError(f"module 'analyzer' has no attribute '{name}'")

