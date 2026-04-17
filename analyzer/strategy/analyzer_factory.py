"""분석 전략 팩토리

분석 타입에 따라 적절한 전략 인스턴스를 생성합니다.

사용법:
    analyzer = AnalyzerFactory.create("framework")
    analyzer = AnalyzerFactory.create("dbms")
"""

from .base_analyzer import AnalyzerStrategy


class AnalyzerFactory:
    """분석 전략 팩토리
    
    지원 전략:
    - "framework": Java/Kotlin 코드 분석
    - "dbms": PL/SQL 코드 분석
    """

    _strategies: dict[str, type] = {}

    @classmethod
    def create(cls, strategy_type: str) -> AnalyzerStrategy:
        strategy_type = (strategy_type or "dbms").lower()
        if not cls._strategies:
            cls._load_strategies()
        strategy_class = cls._strategies.get(strategy_type)
        if not strategy_class:
            available = ", ".join(cls._strategies.keys())
            raise ValueError(f"지원하지 않는 분석 전략: {strategy_type}. 사용 가능: {available}")
        return strategy_class()

    @classmethod
    def _load_strategies(cls) -> None:
        from .dbms.pipeline.dbms_pipeline import DbmsPipeline
        from .framework.pipeline.framework_pipeline import FrameworkPipeline
        cls._strategies = {
            "framework": FrameworkPipeline,
            "dbms": DbmsPipeline,
        }
