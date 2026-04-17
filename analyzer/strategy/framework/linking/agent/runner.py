"""Phase 2 링킹용 에이전트 public API 집합.

- `run_deep_agent_entity_identification`: Entity/Table DeepAgent (LLM)
- `run_deep_agent`: 미해결 참조 DeepAgent (LLM)
- `run_variable_references`: 변수→함수 REFERENCES 결정론적 매칭 (LLM 미사용)
"""

from analyzer.strategy.framework.linking.agent.core import run_deep_agent
from analyzer.strategy.framework.linking.agent.entity import run_deep_agent_entity_identification
from analyzer.strategy.framework.linking.agent.varref import run_variable_references

__all__ = [
    "run_deep_agent",
    "run_deep_agent_entity_identification",
    "run_variable_references",
]
