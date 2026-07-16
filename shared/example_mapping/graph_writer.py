"""Example Mapping 그래프 적재 — ExampleMappingResult → Neo4j.

builder.py(순수) 가 만든 노드·엣지 dataclass 리스트를 Neo4j 에 박는다.
노드(Rule/Example/Question) 먼저 → 엣지 — 엣지 MATCH 가 노드를 찾을 수 있어야 함.
"""
from __future__ import annotations

from shared.example_mapping.builder import ExampleMappingResult
from graph.edges.affects_table import upsert_affects_table
from graph.edges.has_example import upsert_has_example
from graph.edges.has_question import upsert_has_question
from graph.edges.has_rule import upsert_has_rule
from graph.neo4j_client import Neo4jClient
from graph.nodes.example import upsert_examples
from graph.nodes.question import upsert_questions
from graph.nodes.rule import upsert_rules


async def write_example_mapping(client: Neo4jClient, em: ExampleMappingResult) -> None:
    """EM 노드 → 엣지 순서로 Neo4j 적재."""
    await upsert_rules(client, em.rules)
    await upsert_examples(client, em.examples)
    await upsert_questions(client, em.questions)
    await upsert_has_rule(client, em.has_rule)
    await upsert_has_example(client, em.has_example)
    await upsert_has_question(client, em.has_question)
    await upsert_affects_table(client, em.affects_table)
