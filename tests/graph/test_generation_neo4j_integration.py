"""Opt-in real-Neo4j rollback proof for the closed product publication path."""
from __future__ import annotations

import os
import unittest
import uuid

from neo4j import AsyncGraphDatabase

from graph.generation import begin_generation, publish_generation
from graph.product_batch_writer import write_product_batch
from graph.product_write_batch import (
    ProductWriteBatch,
    combine_product_write_batches,
)
from graph.publication_schema import PRODUCT_STAGE_NAMES
from pipeline.publication.analysis_health import build_analysis_health_write_batch
from shared.config.settings import SETTINGS


def _minimal_complete_product_plan(
    *, analysis_scope_id: str, generation_id: str,
):
    batches = [
        ProductWriteBatch.build(
            stage_name=stage_name,
            analysis_scope_id=analysis_scope_id,
            generation_id=generation_id,
        )
        for stage_name in sorted(PRODUCT_STAGE_NAMES - {"analysis_health"})
    ]
    batches.append(build_analysis_health_write_batch(
        analysis_scope_id=analysis_scope_id,
        generation_id=generation_id,
        missing_total=0,
        duplicate_total=0,
        unresolved_call_count=1,
        unresolved_data_object_count=0,
        prepublish_wall_time_ms=1,
        prepublish_performance_artifact_hash="b" * 64,
    ))
    return combine_product_write_batches(
        batches,
        analysis_scope_id=analysis_scope_id,
        generation_id=generation_id,
    )


class _SingleRollbackTransactionClient:
    """Run every managed-write callback in one caller-owned transaction."""

    def __init__(
        self, transaction, *, database: str, analysis_scope_id: str,
        generation_id: str,
    ):
        self.transaction = transaction
        self.database = database
        self.analysis_scope_id = analysis_scope_id
        self.generation_id = generation_id

    async def execute_write(self, callback):
        return await callback(self.transaction)


@unittest.skipUnless(
    os.environ.get("ROBO_RUN_NEO4J_INTEGRATION") == "1",
    "set ROBO_RUN_NEO4J_INTEGRATION=1 for real Neo4j rollback proof",
)
class Neo4jGenerationIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_complete_product_publish_is_visible_then_fully_rolled_back(self) -> None:
        settings = SETTINGS.graph_database
        database = "test"
        unique = uuid.uuid4().hex
        analysis_scope_id = f"analysis-scope/v1:{unique}{unique}"
        generation_id = f"integration-{unique}"
        plan = _minimal_complete_product_plan(
            analysis_scope_id=analysis_scope_id,
            generation_id=generation_id,
        )
        call_ledger = [{
            "call_id": f"integration:{unique}:call:0",
            "ordinal": 0,
            "caller_id": f"integration:{unique}:source",
            "resolution_status": "configuration_dependent",
            "adapter_schema": "language-neutral-resolution/v1",
            "outcome": "unresolved",
            "reason": "insufficient_preprocessing_build_context",
            "scope": None,
            "callee_id": "",
            "external_identity_key": "",
            "declaration_fact_id": "",
            "candidate_fact_ids": [],
            "preprocessing_evidence_ids": ["a" * 64],
            "macro_fact_ids": [],
        }]

        driver = AsyncGraphDatabase.driver(
            settings.uri,
            auth=(settings.user, settings.password),
        )
        try:
            async with driver.session(database=database) as session:
                transaction = await session.begin_transaction()
                client = _SingleRollbackTransactionClient(
                    transaction,
                    database=database,
                    analysis_scope_id=analysis_scope_id,
                    generation_id=generation_id,
                )
                try:
                    await begin_generation(
                        client,
                        generation_id=generation_id,
                        strategy="framework",
                        source_ast_closure={"sha256": "c" * 64},
                    )
                    for batch in plan.batches:
                        await write_product_batch(client, batch)
                    closure = await publish_generation(
                        client,
                        generation_id=generation_id,
                        quality_status="completed",
                        call_resolution_ledger=call_ledger,
                        product_plan=plan,
                    )

                    active = await (
                        await transaction.run(
                            """
MATCH (scope:AnalysisScope {
  analysis_scope_id: $analysis_scope_id
})
RETURN scope.active_generation_id AS generation_id
""",
                            analysis_scope_id=analysis_scope_id,
                        )
                    ).single(strict=True)
                    self.assertEqual(generation_id, active["generation_id"])
                    self.assertEqual(14, closure["product_stages"]["count"])
                    self.assertEqual(1, closure["product_records"]["count"])
                    self.assertEqual(
                        "completed_with_findings", closure["quality_status"],
                    )
                    self.assertEqual(1, closure["call_occurrences"]["count"])
                finally:
                    await transaction.rollback()

            async with driver.session(database=database) as session:
                result = await session.run(
                    """
MATCH (n {analysis_scope_id: $analysis_scope_id})
RETURN count(n) AS count
""",
                    analysis_scope_id=analysis_scope_id,
                )
                self.assertEqual(0, (await result.single(strict=True))["count"])
        finally:
            await driver.close()


if __name__ == "__main__":
    unittest.main()
