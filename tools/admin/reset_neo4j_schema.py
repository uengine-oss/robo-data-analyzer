"""Explicitly rebuild only Analyzer-owned Neo4j schema objects.

This maintenance command is deliberately fail-closed. It requires an allowed
target database, an exact per-database confirmation token, and a successful
inventory/online-state gate before creating a target client. Foreign schema is
never dropped.
"""
from __future__ import annotations

import argparse
import asyncio
import re
import sys
from pathlib import Path
from typing import Sequence


_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from neo4j import AsyncGraphDatabase

from graph.connection_context import Neo4jOverride, get_override, set_override
from graph.neo4j_client import Neo4jClient
from graph.publication_schema import SEARCH_SEGMENT_VECTOR_INDEX_NAME
from graph.schema.constraints import CONSTRAINTS, ensure_schema
from shared.config.settings import SETTINGS


EXPECTED_DATABASES = frozenset({"dbms", "framework", "neo4j", "system", "test"})
WRITABLE_DATABASES = EXPECTED_DATABASES - {"system"}
_CONSTRAINT_NAME = re.compile(r"^CREATE CONSTRAINT\s+([^\s]+)", re.IGNORECASE)
ANALYZER_CONSTRAINT_NAMES = tuple(
    match.group(1)
    for statement in CONSTRAINTS
    if (match := _CONSTRAINT_NAME.match(statement)) is not None
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database",
        required=True,
        choices=sorted(WRITABLE_DATABASES),
        help="existing writable database to repair",
    )
    parser.add_argument(
        "--confirm",
        required=True,
        help="must exactly equal RESET-ANALYZER-SCHEMA:<database>",
    )
    return parser


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    args = build_parser().parse_args(argv)
    expected = f"RESET-ANALYZER-SCHEMA:{args.database}"
    if args.confirm != expected:
        raise SystemExit(f"confirmation mismatch; expected {expected}")
    return args


async def inspect_databases() -> list[dict]:
    """Read the DBMS inventory through the system database without mutations."""
    settings = SETTINGS.graph_database
    driver = AsyncGraphDatabase.driver(
        settings.uri,
        auth=(settings.user, settings.password),
    )
    try:
        async with driver.session(database="system") as session:
            result = await session.run(
                "SHOW DATABASES YIELD name, currentStatus "
                "RETURN name, currentStatus ORDER BY name"
            )
            return await result.data()
    finally:
        await driver.close()


def require_safe_inventory(rows: list[dict], *, target: str) -> None:
    statuses = {
        str(row.get("name")): str(row.get("currentStatus", "")).lower()
        for row in rows
    }
    if set(statuses) != EXPECTED_DATABASES:
        raise RuntimeError(
            "database inventory gate failed: expected only "
            f"{sorted(EXPECTED_DATABASES)}, got {sorted(statuses)}"
        )
    offline = sorted(name for name, status in statuses.items() if status != "online")
    if offline:
        raise RuntimeError(f"database online-state gate failed: {offline}")
    if target not in WRITABLE_DATABASES:
        raise RuntimeError(f"database is not an allowed maintenance target: {target}")


async def reset_analyzer_schema(*, database: str) -> tuple[int, int]:
    """Drop exact Analyzer-owned names and then declare the current schema."""
    require_safe_inventory(await inspect_databases(), target=database)

    settings = SETTINGS.graph_database
    previous_override = get_override()
    set_override(Neo4jOverride(
        settings.uri,
        settings.user,
        settings.password,
        database,
    ))
    client: Neo4jClient | None = None
    try:
        client = Neo4jClient()
        existing_constraints = {
            str(row["name"])
            for row in await client.run_read_query("SHOW CONSTRAINTS YIELD name")
        }
        existing_indexes = {
            str(row["name"])
            for row in await client.run_read_query("SHOW INDEXES YIELD name")
        }

        dropped_constraints = 0
        for name in ANALYZER_CONSTRAINT_NAMES:
            if name in existing_constraints:
                await client.run_query(f"DROP CONSTRAINT `{name}` IF EXISTS")
                dropped_constraints += 1

        dropped_indexes = 0
        if SEARCH_SEGMENT_VECTOR_INDEX_NAME in existing_indexes:
            await client.run_query(
                f"DROP INDEX `{SEARCH_SEGMENT_VECTOR_INDEX_NAME}` IF EXISTS"
            )
            dropped_indexes = 1

        await ensure_schema(client)
        return dropped_constraints, dropped_indexes
    finally:
        if client is not None:
            await client.close()
        set_override(previous_override)


async def async_main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    dropped_constraints, dropped_indexes = await reset_analyzer_schema(
        database=args.database,
    )
    print(
        f"rebuilt Analyzer schema in {args.database}: "
        f"dropped {dropped_constraints} owned constraints and "
        f"{dropped_indexes} owned indexes"
    )
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    return asyncio.run(async_main(argv))


if __name__ == "__main__":
    raise SystemExit(main())
