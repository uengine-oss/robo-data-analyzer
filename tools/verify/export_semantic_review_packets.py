"""Export sealed target packets for direct human semantic judgment.

The exporter calls no model and performs no semantic judgment. It joins the actual
prompt/parsed response, deterministic Rule source, recorded findings, and the exact
active graph generation identified by one run manifest. Targets are either supplied
explicitly or taken exhaustively from that run's prompt records.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import hashlib
import json
import os
from pathlib import Path
import sys
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from dotenv import load_dotenv
from neo4j import GraphDatabase

from pipeline.quality_gates.rule_audit_artifact import load_rule_audit_records
from tools.verify.inventory_semantic_outputs import _records, _run_identity
from search.corpus.generation import completed_generation_session


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--target-id", action="append", default=[])
    parser.add_argument("--database")
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    return parser.parse_args()


def _canonical_json(value: object) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    )


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _json_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON artifact must be an object: {path}")
    return value


def _prompt_records(run_dir: Path) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for path in sorted((run_dir / "llm-prompts").glob("*.jsonl")):
        concern = path.stem
        for record in _records(path):
            target_id = str(record.get("target_id") or "").strip()
            if not target_id:
                raise ValueError(f"prompt record has no audit target: {path}")
            grouped[target_id].append({
                "concern": concern,
                "system_prompt": str(record.get("system_prompt") or ""),
                "user_content": str(record.get("user_content") or ""),
                "response_schema": record.get("response_schema"),
                "parsed_response": (record.get("response") or {}).get("parsed"),
                "raw_response_sha256": _sha256_text(
                    str((record.get("response") or {}).get("raw") or "")
                ),
                "metadata": record.get("metadata") or {},
            })
    for records in grouped.values():
        records.sort(key=lambda item: item["concern"])
    return dict(grouped)


def _finding_records(run_dir: Path) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in _records(run_dir / "analysis-findings.jsonl"):
        target_id = str(record.get("target_id") or "")
        owner_id = target_id.rpartition("::R-")[0] or target_id
        grouped[owner_id].append(record)
    return dict(grouped)


def _selected_targets(
    available: set[str], requested: list[str],
) -> list[str]:
    selected = sorted(set(str(value).strip() for value in requested if str(value).strip()))
    if not selected:
        return sorted(available)
    missing = sorted(set(selected) - available)
    if missing:
        raise ValueError(
            f"requested target has no prompt record: first={missing[0]!r}, "
            f"count={len(missing)}"
        )
    return selected


def _graph_records(
    *,
    database: str,
    env_file: Path,
    analysis_scope_id: str,
    generation_id: str,
    target_ids: list[str],
) -> dict[str, dict[str, Any]]:
    load_dotenv(env_file)
    driver = GraphDatabase.driver(
        os.environ["ROBO_NEO4J_URI"],
        auth=(os.environ["ROBO_NEO4J_USER"], os.environ["ROBO_NEO4J_PASSWORD"]),
    )
    try:
        with completed_generation_session(
            driver,
            database,
            analysis_scope_id=analysis_scope_id,
            expected_generation_id=generation_id,
        ) as session:
            nodes = {
                str(row["stable_id"]): {
                    "labels": list(row["labels"]),
                    "properties": dict(row["properties"]),
                }
                for row in session.run(
                    "UNWIND $target_ids AS target_id "
                    "MATCH (node {stable_id:target_id, "
                    "analysis_scope_id:$analysis_scope_id, generation_id:$generation_id}) "
                    "RETURN node.stable_id AS stable_id, labels(node) AS labels, "
                    "properties(node) AS properties",
                    target_ids=target_ids,
                )
            }
            rule_rows = [
                dict(row)
                for row in session.run(
                    "UNWIND $target_ids AS target_id "
                    "MATCH (owner:CodeNode {stable_id:target_id, "
                    "analysis_scope_id:$analysis_scope_id, generation_id:$generation_id})"
                    "-[owns:HAS_RULE]->(rule:Rule {analysis_scope_id:$analysis_scope_id, "
                    "generation_id:$generation_id}) "
                    "OPTIONAL MATCH (rule)-[shows:HAS_EXAMPLE]->"
                    "(example:Example {analysis_scope_id:$analysis_scope_id, "
                    "generation_id:$generation_id}) "
                    "RETURN owner.stable_id AS target_id, properties(rule) AS rule, "
                    "owns.ordinal AS rule_ordinal, properties(example) AS example, "
                    "shows.ordinal AS example_ordinal "
                    "ORDER BY target_id, rule_ordinal, example_ordinal",
                    target_ids=target_ids,
                )
            ]
            column_rows = [
                dict(row)
                for row in session.run(
                    "UNWIND $target_ids AS target_id "
                    "MATCH (table:Table {stable_id:target_id, "
                    "analysis_scope_id:$analysis_scope_id, generation_id:$generation_id})"
                    "-[:HAS_COLUMN]->(column:Column {analysis_scope_id:$analysis_scope_id, "
                    "generation_id:$generation_id}) "
                    "RETURN table.stable_id AS target_id, properties(column) AS column "
                    "ORDER BY target_id, column.stable_id",
                    target_ids=target_ids,
                )
            ]
    finally:
        driver.close()

    rules_by_target: dict[str, list[dict[str, Any]]] = defaultdict(list)
    seen_rules: set[tuple[str, str]] = set()
    for row in rule_rows:
        target_id = str(row["target_id"])
        rule = dict(row.get("rule") or {})
        rule_id = str(rule.get("stable_id") or "")
        key = (target_id, rule_id)
        if key not in seen_rules:
            rules_by_target[target_id].append({
                "ordinal": row.get("rule_ordinal"),
                "properties": rule,
                "examples": [],
            })
            seen_rules.add(key)
        example = row.get("example")
        if example:
            rules_by_target[target_id][-1]["examples"].append({
                "ordinal": row.get("example_ordinal"),
                "properties": dict(example),
            })
    columns_by_target: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in column_rows:
        columns_by_target[str(row["target_id"])].append(dict(row["column"]))

    return {
        target_id: {
            "node": nodes.get(target_id),
            "rules": rules_by_target.get(target_id, []),
            "columns": columns_by_target.get(target_id, []),
        }
        for target_id in target_ids
    }


def main() -> int:
    args = _args()
    run_dir = args.run_dir.resolve()
    manifest = _json_object(run_dir / "manifest.json")
    database = str(args.database or manifest.get("neo4j_database") or "").strip()
    if not database:
        raise ValueError("run manifest has no Neo4j database")
    analysis_scope_id, generation_id = _run_identity(run_dir, database)
    prompts = _prompt_records(run_dir)
    findings = _finding_records(run_dir)
    rule_structures = load_rule_audit_records(run_dir)
    targets = _selected_targets(set(prompts), args.target_id)
    graph = _graph_records(
        database=database,
        env_file=args.env_file,
        analysis_scope_id=analysis_scope_id,
        generation_id=generation_id,
        target_ids=targets,
    )
    packets = [
        {
            "analysis_scope_id": analysis_scope_id,
            "generation_id": generation_id,
            "target_id": target_id,
            "model_interactions": prompts[target_id],
            "deterministic_rule_structure": rule_structures.get(target_id),
            "analysis_findings": findings.get(target_id, []),
            "published_graph": graph[target_id],
        }
        for target_id in targets
    ]
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    body = "".join(_canonical_json(packet) + "\n" for packet in packets)
    output.write_text(body, encoding="utf-8", newline="\n")
    result = {
        "run_dir": str(run_dir),
        "analysis_scope_id": analysis_scope_id,
        "generation_id": generation_id,
        "targets": len(targets),
        "output": str(output),
        "output_sha256": _sha256_text(body),
        "bytes": len(body.encode("utf-8")),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
