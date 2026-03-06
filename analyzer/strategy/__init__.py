"""분석 전략 패턴

Framework(Java/Kotlin)와 DBMS(PL/SQL) 분석을 위한 전략 패턴 구현.

구조 설계:
- AnalyzerStrategy: 추상 기본 인터페이스
- BaseStreamingAnalyzer: 공통 프레임 담당 (Neo4j 초기화, 리소스 정리 등)
- FrameworkAnalyzer: Framework(Java/Kotlin) 분석 파이프라인
- DbmsAnalyzer: DBMS(PL/SQL) 분석 파이프라인

사용법:
    analyzer = AnalyzerFactory.create("framework")
    async for chunk in analyzer.analyze(files, orchestrator):
        yield chunk
"""

from .base_analyzer import AnalyzerStrategy, BaseStreamingAnalyzer, AnalysisStats
from .analyzer_factory import AnalyzerFactory

__all__ = [
    "AnalyzerStrategy",
    "BaseStreamingAnalyzer",
    "AnalysisStats",
    "AnalyzerFactory",
]
