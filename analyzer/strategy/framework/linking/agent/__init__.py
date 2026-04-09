"""DeepAgent 통합 패키지.

3종의 DeepAgent (엔티티 식별, 변수 참조, 미해결 추적)를 통합 인터페이스로 제공.
"""

from analyzer.strategy.framework.linking.agent.runner import (
    run_deep_agent,
    run_deep_agent_entity_identification,
    run_deep_agent_variable_references,
)

__all__ = [
    "run_deep_agent",
    "run_deep_agent_entity_identification",
    "run_deep_agent_variable_references",
]
