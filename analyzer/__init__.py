"""ROBO Analyzer 분석 모듈.

소스 코드를 분석하여 Neo4j 그래프로 변환하는 핵심 모듈.

주요 구성:
- strategy: 분석 전략 패턴 (Framework, DBMS)

호출자는 서브모듈에서 직접 import:
    from analyzer.strategy import AnalyzerFactory, AnalyzerStrategy
    from client.neo4j_client import Neo4jClient

본 패키지 __init__ 는 빈 namespace 로 둔다 — 진짜 순환 import 차단:
client.neo4j_client → analyzer.common.pipeline.pipeline_control 경로로
analyzer 패키지가 트리거될 때, __init__ 가 strategy 를 미리 import 하면
strategy → base_analyzer → client.neo4j_client 로 순환 발생.
"""
