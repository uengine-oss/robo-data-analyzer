"""분석 전략 팩토리

분석 타입에 따라 적절한 전략 인스턴스를 생성합니다.

사용법:
    analyzer = AnalyzerFactory.create("framework")
    analyzer = AnalyzerFactory.create("dbms")
"""

from .base_analyzer import AnalyzerStrategy
from .dbms.pipeline.dbms_pipeline import DbmsPipeline
from .framework.pipeline.framework_pipeline import FrameworkPipeline


class AnalyzerFactory:
    """분석 전략 팩토리

    지원 전략:
    - "framework": Java/Kotlin/C 등 소스코드 분석
    - "dbms": PL/SQL 등 DBMS 코드 분석
    """

    _strategies: dict[str, type] = {
        "framework": FrameworkPipeline,
        "dbms": DbmsPipeline,
    }

    @classmethod
    def create(cls, strategy_type: str) -> AnalyzerStrategy:
        if not strategy_type:
            raise ValueError("strategy_type 필수 — service 진입점에서 검증되어 전달되어야 함")
        strategy_type = strategy_type.lower()
        strategy_class = cls._strategies.get(strategy_type)
        if not strategy_class:
            available = ", ".join(cls._strategies.keys())
            raise ValueError(f"지원하지 않는 분석 전략: {strategy_type}. 사용 가능: {available}")
        return strategy_class()
