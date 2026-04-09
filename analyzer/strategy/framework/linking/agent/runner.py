"""DeepAgent 통합 실행기.

agent/ 패키지 내부의 core.py, entity.py, varref.py에서 공개 함수를 통합 제공.
"""

from __future__ import annotations

from analyzer.strategy.framework.linking.agent.core import (
    run_deep_agent,
    run_deep_agent_entity_identification,
)
from analyzer.strategy.framework.linking.agent.varref import (
    run_deep_agent_variable_references,
)

__all__ = [
    "run_deep_agent",
    "run_deep_agent_entity_identification",
    "run_deep_agent_variable_references",
]
