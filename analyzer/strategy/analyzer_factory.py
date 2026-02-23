"""분석 전략 팩토리

분석 타입에 따라 적절한 전략 인스턴스를 생성합니다.

사용법:
    analyzer = AnalyzerFactory.create("framework")
    analyzer = AnalyzerFactory.create("dbms")
"""

from typing import TYPE_CHECKING

from .base_analyzer import AnalyzerStrategy

if TYPE_CHECKING:
    from .framework.framework_pipeline import FrameworkAnalyzer
    from .dbms.dbms_pipeline import DbmsPipeline


class AnalyzerFactory:
    """분석 전략 팩토리
    
    지원 전략:
    - "framework": Java/Kotlin 코드 분석
    - "dbms": PL/SQL 코드 분석
    """

    _strategies: dict[str, type] = {}

    @classmethod
    def create(cls, strategy_type: str) -> AnalyzerStrategy:
        """전략 인스턴스 생성
        
        Args:
            strategy_type: "framework" 또는 "dbms"
            
        Returns:
            AnalyzerStrategy 인스턴스
            
        Raises:
            ValueError: 지원하지 않는 전략 타입인 경우
        """
        strategy_type = (strategy_type or "dbms").lower()
        
        # 지연 로딩으로 순환 참조 방지
        if not cls._strategies:
            cls._load_strategies()
        
        strategy_class = cls._strategies.get(strategy_type)
        if not strategy_class:
            available = ", ".join(cls._strategies.keys())
            raise ValueError(
                f"지원하지 않는 분석 전략: {strategy_type}. "
                f"사용 가능: {available}"
            )
        
        return strategy_class()

    @classmethod
    def _load_strategies(cls) -> None:
        """전략 클래스 지연 로딩"""
        from .framework.framework_pipeline import FrameworkAnalyzer
        from .dbms.dbms_pipeline import DbmsPipeline
        
        cls._strategies = {
            "framework": FrameworkAnalyzer,
            "dbms": DbmsPipeline,
        }
