"""Opt-in real-Neo4j transaction proof for spec 131 generation publication."""
from __future__ import annotations

import os
import unittest
import uuid

from neo4j import AsyncGraphDatabase

from graph.generation import publish_generation
from graph.ownership import ANALYZER_GRAPH_OWNER
from pipeline.models.call_resolution import call_resolution_storage_item
from shared.config.settings import SETTINGS


class _RollbackPublishClient:
    def __init__(self, driver, database: str, generation_id: str, ledger: list[dict]):
        self.driver = driver
        self.database = database
        self.generation_id = generation_id
        self.ledger = ledger

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
                    semantic_frame_json=None,
                )
                occurrence_items = [
                    call_resolution_storage_item(row) for row in self.ledger
                ]
                if occurrence_items:
                    await transaction.run(
                        """
UNWIND $items AS item
MATCH (owner:ANALYZER_OWNED {
  graph_owner: $graph_owner,
  generation_id: $generation_id,
  id: item.caller_id
})
CREATE (occurrence:ANALYZER_OWNED:CALL_OCCURRENCE)
SET occurrence = item,
    occurrence.graph_owner = $graph_owner,
    occurrence.generation_id = $generation_id
CREATE (owner)-[:HAS_CALL_OCCURRENCE {
  generation_id: $generation_id,
  call_id: item.call_id,
  ordinal: item.ordinal
}]->(occurrence)
""",
                        graph_owner=ANALYZER_GRAPH_OWNER,
                        generation_id=self.generation_id,
                        items=occurrence_items,
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
            source_id = f"integration:{generation_id}:source"
            ledger = [{
                "call_id": f"integration:{generation_id}:call:0",
                "ordinal": 0,
                "source_id": f"integration:{generation_id}:file",
                "caller_id": source_id,
                "owner_status": "resolved",
                "file": "integration.c",
                "line": 7,
                "range": {"char_offset": 20, "char_length": 6},
                "callee_range": {"char_offset": 20, "char_length": 3},
                "argument_ranges": [],
                "callee_kind": "named",
                "terminal_name": "run",
                "grammar_rule": "postfixExpression",
                "presence_status": "active",
                "presence": {
                    "status": "active",
                    "condition": None,
                    "configurationId": None,
                    "provenance": "unconditional",
                },
                "configured_preprocessing_required": True,
                "outcome": "unresolved",
                "reason": "insufficient_preprocessing_build_context",
                "scope": None,
                "callee_id": "",
                "preprocessing_evidence_ids": ["a" * 64],
                "macro_fact_ids": [],
            }]

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
                _RollbackPublishClient(driver, "test", generation_id, ledger),
                generation_id=generation_id,
                quality_status="completed",
                call_resolution_ledger=ledger,
            )
            self.assertEqual(3, closure["nodes"]["count"])
            self.assertEqual(2, closure["edges"]["count"])
            self.assertEqual(0, closure["slots"]["count"])
            self.assertEqual("completed_with_findings", closure["quality_status"])
            self.assertEqual(1, closure["call_occurrences"]["count"])
            self.assertEqual(1, closure["call_occurrences"]["unresolved"])
            self.assertEqual(ledger, closure["call_occurrences"]["identities"])

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
