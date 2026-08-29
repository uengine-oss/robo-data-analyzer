"""Create the current Analyzer Neo4j constraints once at run start.

The runtime only declares the schema required by the current publication model.
Historical schema cleanup is an explicit maintenance operation, never part of an
analysis run.
"""
from __future__ import annotations

from graph.neo4j_client import Neo4jClient
from graph.publication_schema import PRODUCT_NODE_TYPES


PRODUCT_CONSTRAINTS: tuple[str, ...] = tuple(
    "CREATE CONSTRAINT "
    f"uniq_product_{label.lower()}_scope_generation IF NOT EXISTS "
    f"FOR (n:{label}) "
    "REQUIRE (n.analysis_scope_id, n.generation_id, n.stable_id) IS UNIQUE"
    for label in sorted(PRODUCT_NODE_TYPES)
)

CONSTRAINTS: list[str] = [
    "CREATE CONSTRAINT uniq_analyzer_owned_scope_generation IF NOT EXISTS "
    "FOR (n:ANALYZER_OWNED) "
    "REQUIRE (n.graph_owner, n.analysis_scope_id, n.generation_id, n.id) IS UNIQUE",
    "CREATE CONSTRAINT uniq_analysis_generation IF NOT EXISTS "
    "FOR (g:AnalysisGeneration) "
    "REQUIRE (g.graph_owner, g.analysis_scope_id, g.generation_id) IS UNIQUE",
    "CREATE CONSTRAINT uniq_analysis_scope IF NOT EXISTS "
    "FOR (s:AnalysisScope) REQUIRE (s.graph_owner, s.analysis_scope_id) IS UNIQUE",
] + list(PRODUCT_CONSTRAINTS)

# No standalone lookup index is required by the current scoped product queries.
INDEXES: list[str] = []


async def ensure_schema(client: Neo4jClient) -> None:
    """Declare the current stable schema without inspecting or migrating history."""
    for statement in CONSTRAINTS + INDEXES:
        await client.run_query(statement)
