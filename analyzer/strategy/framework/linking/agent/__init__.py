"""DeepAgent 패키지.

3종의 분석 모듈을 통합 인터페이스로 제공:
  - run_deep_agent: 미해결 참조 추적 (Skill 기반 배치 처리)
  - run_deep_agent_entity_identification: 엔티티 식별 + Function→Table
  - run_variable_references: 변수 참조 결정론적 매칭 (LLM 미사용)
"""

from analyzer.strategy.framework.linking.agent.core import run_deep_agent
from analyzer.strategy.framework.linking.agent.entity import run_deep_agent_entity_identification
from analyzer.strategy.framework.linking.agent.varref import run_variable_references
from analyzer.strategy.framework.linking.agent.tools import ToolResultCollector

__all__ = [
    "run_deep_agent",
    "run_deep_agent_entity_identification",
    "run_variable_references",
    "ToolResultCollector",
]
