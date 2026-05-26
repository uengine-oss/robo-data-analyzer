"""Example Mapping — analyze_function 3분할 결과 → Rule/Example/Question 그래프.

- ``build_example_mapping`` : 분석된 Function → 노드·엣지 (순수, LLM 0).
- ``write_example_mapping``  : 그 결과를 Neo4j 적재.
"""
from shared.example_mapping.builder import (
    ExampleMappingResult,
    build_example_mapping,
    rule_hash,
)
from shared.example_mapping.graph_writer import write_example_mapping

__all__ = [
    "ExampleMappingResult",
    "build_example_mapping",
    "rule_hash",
    "write_example_mapping",
]
