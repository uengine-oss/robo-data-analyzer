"""Seal a deterministic cross-node semantic sample without reading semantic outputs.

Selection uses only database name, category, stable IDs, owner IDs, and source coordinates.
Summary/NL/description text is intentionally absent from every selection query.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from dotenv import dotenv_values
from neo4j import GraphDatabase


SEED = "spec131-cross-node-semantic-v1"
QUOTAS = {
    "function_summary": {"shopmallseq": 4, "rwisverify": 4},
    "rule": {"shopmallseq": 5, "rwisverify": 5},
    "example": {"shopmallseq": 4, "rwisverify": 4},
    "table": {"shopmallseq": 3, "rwisverify": 3},
    "column": {"shopmallseq": 4, "rwisverify": 4},
}

QUERIES = {
    "function_summary": """
        MATCH (n)
        WHERE (n:FUNCTION OR n:PROCEDURE)
          AND NOT n:EXTERNAL
          AND n.analysis_status = 'completed'
          AND n.summary IS NOT NULL
        RETURN n.id AS id, n.owner_id AS owner_id,
               n.file_path AS source_file, n.start_line AS source_line
    """,
    "rule": """
        MATCH (owner)-[:HAS_RULE]->(n:RULE)
        RETURN n.id AS id, owner.id AS owner_id,
               owner.file_path AS source_file, n.anchor_line AS source_line
    """,
    "example": """
        MATCH (owner)-[:HAS_RULE]->(rule:RULE)-[:HAS_EXAMPLE]->(n:EXAMPLE)
        RETURN n.id AS id, owner.id AS owner_id, rule.id AS rule_id,
               owner.file_path AS source_file, rule.anchor_line AS source_line
    """,
    "table": """
        MATCH (n:TABLE)
        WHERE n.analyzed_description IS NOT NULL
        RETURN n.id AS id, null AS owner_id, null AS source_file, null AS source_line
    """,
    "column": """
        MATCH (table:TABLE)-[:HAS_COLUMN]->(n:COLUMN)
        WHERE n.analyzed_description IS NOT NULL
        RETURN n.id AS id, table.id AS owner_id, null AS source_file, null AS source_line
    """,
}


def _rank(database: str, category: str, item: dict) -> str:
    stable = f"{SEED}|{database}|{category}|{item['id']}"
    return hashlib.sha256(stable.encode("utf-8")).hexdigest()


def _connection() -> tuple[str, str, str]:
    config = dotenv_values(Path(__file__).parents[2] / ".env")
    uri = config.get("ROBO_NEO4J_URI") or config.get("NEO4J_URI")
    user = (config.get("ROBO_NEO4J_USERNAME") or config.get("ROBO_NEO4J_USER")
            or config.get("NEO4J_USERNAME") or config.get("NEO4J_USER"))
    password = config.get("ROBO_NEO4J_PASSWORD") or config.get("NEO4J_PASSWORD")
    if not uri or not user or not password:
        raise RuntimeError("Neo4j read-only connection values are missing")
    return uri, user, password


def seal() -> dict:
    uri, user, password = _connection()
    populations: dict[str, dict[str, int]] = {}
    selected: list[dict] = []
    with GraphDatabase.driver(uri, auth=(user, password)) as driver:
        for category, per_database in QUOTAS.items():
            populations[category] = {}
            for database, quota in per_database.items():
                with driver.session(database=database) as session:
                    rows = [dict(row) for row in session.run(QUERIES[category])]
                unique = {str(row["id"]): row for row in rows if row.get("id")}
                ranked = sorted(
                    unique.values(), key=lambda item: _rank(database, category, item)
                )
                populations[category][database] = len(ranked)
                for item in ranked[: min(quota, len(ranked))]:
                    selected.append({
                        "sample_key": f"{database}:{category}:{item['id']}",
                        "database": database,
                        "category": category,
                        "id": item["id"],
                        "owner_id": item.get("owner_id"),
                        "rule_id": item.get("rule_id"),
                        "source_file": item.get("source_file"),
                        "source_line": item.get("source_line"),
                        "selection_rank": _rank(database, category, item),
                    })
    return {
        "schema_version": "1.0.0",
        "seed": SEED,
        "selection_contract": (
            "IDs/owners/coordinates only; no summary, NL, example, table, or column semantic "
            "output was read by the selector"
        ),
        "quotas": QUOTAS,
        "populations": populations,
        "selected_total": len(selected),
        "selected": selected,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    payload = json.dumps(seal(), ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload, encoding="utf-8")
    else:
        print(payload, end="")


if __name__ == "__main__":
    main()
