"""DeepAgent 통합 실행기 (하위 호환용).

모든 public API는 __init__.py에서 직접 제공.
이 파일은 기존 import 경로 호환을 위해 유지한다.
신규 코드에서는 agent 패키지에서 직접 import할 것.
"""

from analyzer.strategy.framework.linking.agent.core import run_deep_agent
from analyzer.strategy.framework.linking.agent.entity import run_deep_agent_entity_identification
from analyzer.strategy.framework.linking.agent.varref import run_variable_references

# 기존 호환: run_deep_agent_variable_references → run_variable_references
run_deep_agent_variable_references = run_variable_references

__all__ = [
    "run_deep_agent",
    "run_deep_agent_entity_identification",
    "run_deep_agent_variable_references",
    "run_variable_references",
]
