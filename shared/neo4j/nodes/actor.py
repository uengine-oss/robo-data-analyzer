"""Actor 노드 Cypher — 비즈니스 actor (cross-module 통합).

여러 함수의 actor 필드가 동의어로 묶이면 한 Actor 노드로.
``prompts/<strategy>/actor_merge.yaml`` 결과를 받아 박음.
"""
from __future__ import annotations

from typing import Iterable

from data_models.graph_nodes import Actor
from shared.neo4j.neo4j_client import Neo4jClient


MERGE_QUERY = """
UNWIND $items AS item
MERGE (a:Actor {actor_id: item.actor_id})
SET a.display_name = item.display_name,
    a.member_function_ids = item.member_function_ids
RETURN count(a) AS created
"""


def to_item(actor: Actor) -> dict:
    return {
        "actor_id": actor.actor_id,
        "display_name": actor.display_name,
        "member_function_ids": list(actor.member_function_ids),
    }


async def upsert_actors(client: Neo4jClient, actors: Iterable[Actor]) -> None:
    items = [to_item(a) for a in actors]
    await client.run_batch_unwind(MERGE_QUERY, items)
