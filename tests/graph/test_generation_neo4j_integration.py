"""Opt-in real-Neo4j transaction proof for spec 131 generation publication."""
from __future__ import annotations

import os
import unittest
import uuid

from neo4j import AsyncGraphDatabase

from graph.generation import publish_generation
from graph.ownership import ANALYZER_GRAPH_OWNER
from shared.config.settings import SETTINGS


class _RollbackPublishClient:
    def __init__(self, driver, database: str, generation_id: str):
        self.driver = driver
        self.database = database
        self.generation_id = generation_id

    async def execute_write(self, callback):
        async with self.driver.session(database=self.database) as session:
            transaction = await session.begin_transaction()
            try:
                await transaction.run(
                    """
CREATE (:ANALYZER_GENERATION {
  graph_owner: $graph_owner,
  run_id: $generation_id,
  status: 'building'
})
CREATE (source:ANALYZER_OWNED:FUNCTION {
  graph_owner: $graph_owner,
  generation_id: $generation_id,
  id: $source_id,
  semantic_frame_json: $semantic_frame_json
})
CREATE (target:ANALYZER_OWNED:TABLE {
  graph_owner: $graph_owner,
  generation_id: $generation_id,
  id: $target_id
})
CREATE (source)-[:READS {generation_id: $generation_id}]->(target)
""",
                    graph_owner=ANALYZER_GRAPH_OWNER,
                    generation_id=self.generation_id,
                    source_id=f"integration:{self.generation_id}:source",
                    target_id=f"integration:{self.generation_id}:target",
                    semantic_frame_json=(
                        '{"claims":[{"slotId":"SL:test","type":"TEST",'
                        '"status":"sufficient","evidenceRefs":["EV:test"]}]}'
                    ),
                )
                closure = await callback(transaction)
                row = await (
                    await transaction.run(
                        """
MATCH (active:ANALYZER_ACTIVE_GENERATION {
  graph_owner: $graph_owner,
  key: 'active'
})
RETURN active.current_generation_id AS generation_id
""",
                        graph_owner=ANALYZER_GRAPH_OWNER,
                    )
                ).single(strict=True)
                if row["generation_id"] != self.generation_id:
                    raise AssertionError("active pointer was not visible inside publish transaction")
                return closure
            finally:
                await transaction.rollback()


@unittest.skipUnless(
    os.environ.get("ROBO_RUN_NEO4J_INTEGRATION") == "1",
    "set ROBO_RUN_NEO4J_INTEGRATION=1 for real Neo4j rollback proof",
)
class Neo4jGenerationIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_publish_cypher_is_atomic_and_rollback_leaves_database_unchanged(self) -> None:
        settings = SETTINGS.graph_database
        generation_id = f"integration-{uuid.uuid4().hex}"
        driver = AsyncGraphDatabase.driver(
            settings.uri,
            auth=(settings.user, settings.password),
        )
        try:
            async def active_rows() -> list[dict]:
                async with driver.session(database="test") as session:
                    result = await session.run(
                        """
MATCH (active:ANALYZER_ACTIVE_GENERATION {
  graph_owner: $graph_owner,
  key: 'active'
})
RETURN active.current_generation_id AS generation_id
""",
                        graph_owner=ANALYZER_GRAPH_OWNER,
                    )
                    return await result.data()

            active_before = await active_rows()
            closure = await publish_generation(
                _RollbackPublishClient(driver, "test", generation_id),
                generation_id=generation_id,
                quality_status="completed",
            )
            self.assertEqual(2, closure["nodes"]["count"])
            self.assertEqual(1, closure["edges"]["count"])
            self.assertEqual(1, closure["slots"]["count"])

            async with driver.session(database="test") as session:
                result = await session.run(
                    """
MATCH (n)
WHERE n.run_id = $generation_id OR n.generation_id = $generation_id
RETURN count(n) AS count
""",
                    generation_id=generation_id,
                )
                self.assertEqual(0, (await result.single(strict=True))["count"])
            self.assertEqual(active_before, await active_rows())
        finally:
            await driver.close()


if __name__ == "__main__":
    unittest.main()
