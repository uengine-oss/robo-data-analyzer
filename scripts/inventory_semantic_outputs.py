"""Read-only inventory of semantic outputs and their stored graph values.

This tool makes no semantic judgment and calls no LLM.  It exposes silent blanks,
findings, empty deterministic Rule/Example fields, and ambiguous source coordinates
for the exact analysis scope and generation sealed by one run manifest.
"""

from __future__ import annotations

import argparse
from collections import Counter
import json
import os
from pathlib import Path
import sys
from typing import Any, Iterable

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from dotenv import load_dotenv
from neo4j import GraphDatabase

from pipeline.quality_gates.rule_audit_artifact import (
    RULE_AUDIT_DIRECTORY,
    load_rule_audit_records,
)
from search.corpus.generation import completed_generation_session


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--database", required=True)
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    return parser.parse_args()


def _blank(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, (list, tuple, dict)):
        return not value
    return not str(value).strip()


def _records(path: Path) -> Iterable[dict[str, Any]]:
    if not path.exists():
        return
    with path.open(encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, 1):
            if not line.strip():
                continue
            value = json.loads(line)
            props = value.get("properties", value)
            props["_line_no"] = line_no
            yield props


def _slot_values(value: Any) -> dict[str, str]:
    """Return the current exact-key semantic slot map."""
    if not isinstance(value, dict):
        raise ValueError(f"semantic slots must be an object: {type(value).__name__}")
    return {str(key): str(item or "") for key, item in value.items()}


def _run_identity(run_dir: Path, database: str) -> tuple[str, str]:
    """Read the graph identity sealed by this run and reject cross-run joins."""
    manifest_path = run_dir / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"invalid run manifest: {manifest_path}: {error}") from error
    analysis_scope_id = str(manifest.get("analysis_scope_id") or "").strip()
    generation_id = str(manifest.get("generation_id") or "").strip()
    manifest_database = str(manifest.get("neo4j_database") or "").strip()
    if not analysis_scope_id or not generation_id or not manifest_database:
        raise ValueError(f"run manifest graph identity is incomplete: {manifest_path}")
    if manifest_database != database:
        raise ValueError(
            f"run manifest database mismatch: expected={manifest_database!r}, "
            f"requested={database!r}"
        )
    return analysis_scope_id, generation_id


def _prompt_inventory(run_dir: Path) -> tuple[dict[str, Any], set[str], set[str]]:
    prompt_dir = run_dir / "llm-prompts"

    summaries: list[dict[str, Any]] = []
    for path in sorted(prompt_dir.glob("code_summary*.jsonl")):
        for record in _records(path):
            parsed = (record.get("response") or {}).get("parsed") or {}
            summaries.append({
                "summary": parsed.get("summary"),
                "stereotype": parsed.get("stereotype"),
                "slots": _slot_values(parsed.get("slots")),
            })

    rule_directory = run_dir / RULE_AUDIT_DIRECTORY
    if not rule_directory.is_dir():
        raise ValueError(f"current rule structure directory is missing: {rule_directory}")
    audit_records = load_rule_audit_records(run_dir)
    structural_rules: list[dict[str, Any]] = [
        rule
        for record in audit_records.values()
        for rule in record["rules"]
    ]
    rule_calls = 0
    rule_response_rows = 0
    rule_effect_meanings = 0
    empty_rule_effect_meanings = 0
    missing_rule_response_rows = 0
    extra_rule_response_rows = 0
    unexpected_rule_targets = 0
    duplicate_rule_targets = 0
    rule_dispositions: Counter[str] = Counter()
    seen_rule_targets: set[str] = set()
    for path in sorted(prompt_dir.glob("code_rule_meanings*.jsonl")):
        for record in _records(path):
            rule_calls += 1
            target_id = str(record.get("target_id") or "")
            if target_id in seen_rule_targets:
                duplicate_rule_targets += 1
            seen_rule_targets.add(target_id)
            parsed = (record.get("response") or {}).get("parsed") or {}
            response_rows = parsed.get("rows")
            effect_meanings = parsed.get("effects")
            if not isinstance(response_rows, dict) or not isinstance(
                effect_meanings, dict
            ):
                raise ValueError(
                    f"Rule meaning response must contain exact-key row/effect maps: "
                    f"{target_id!r}"
                )
            rule_response_rows += len(response_rows)
            rule_dispositions.update(str(value) for value in response_rows.values())
            rule_effect_meanings += len(effect_meanings)
            empty_rule_effect_meanings += sum(
                _blank(value) for value in effect_meanings.values()
            )
            audit = audit_records.get(target_id)
            if audit is None:
                unexpected_rule_targets += 1
                continue
            expected_rows = set((audit["evidence"].get("rows") or {}).keys())
            actual_rows = set(str(key) for key in response_rows)
            missing_rule_response_rows += len(expected_rows - actual_rows)
            extra_rule_response_rows += len(actual_rows - expected_rows)

    tables: list[dict[str, Any]] = []
    columns: list[dict[str, Any]] = []
    target_table_ids: set[str] = set()
    target_column_ids: set[str] = set()
    table_path = prompt_dir / "table_description.jsonl"
    for record in _records(table_path):
        parsed = (record.get("response") or {}).get("parsed") or {}
        table_id = str(record.get("target_id") or "")
        tables.append({"slots": _slot_values(parsed.get("slots"))})
        if table_id:
            target_table_ids.add(table_id)
        raw_columns = parsed.get("columns")
        if not isinstance(raw_columns, dict):
            raise ValueError("table description columns must be an exact-key object")
        for name, column in raw_columns.items():
            if not isinstance(column, dict):
                raise ValueError(f"column semantic frame must be an object: {name!r}")
            columns.append({
                "name": str(name),
                "slots": _slot_values(column.get("slots")),
            })
            if table_id and name:
                target_column_ids.add(f"{table_id}.{name}")

    findings_summary_path = run_dir / "analysis_findings_summary.json"
    findings_summary = (
        json.loads(findings_summary_path.read_text(encoding="utf-8"))
        if findings_summary_path.exists()
        else None
    )
    findings_path = run_dir / "analysis-findings.jsonl"
    finding_rows = sum(1 for _ in _records(findings_path))

    return (
        {
            "summary": {
                "outputs": len(summaries),
                "empty_summary": sum(_blank(item.get("summary")) for item in summaries),
                "empty_stereotype": sum(_blank(item.get("stereotype")) for item in summaries),
                "slots": sum(len(item["slots"]) for item in summaries),
                "empty_slots": sum(
                    _blank(value)
                    for item in summaries
                    for value in item["slots"].values()
                ),
            },
            "rule": {
                "model_calls": rule_calls,
                "obligation_rows": len(structural_rules),
                "response_rows": rule_response_rows,
                "business_rule_rows": rule_dispositions.get("business_rule", 0),
                "implementation_detail_rows": rule_dispositions.get(
                    "implementation_detail", 0
                ),
                "unknown_disposition_rows": sum(
                    count
                    for disposition, count in rule_dispositions.items()
                    if disposition not in {"business_rule", "implementation_detail"}
                ),
                "empty_structural_condition_rows": sum(
                    _blank(item.get("structural_condition"))
                    for item in structural_rules
                ),
                "empty_structural_effect_rows": sum(
                    _blank(item.get("structural_effects"))
                    for item in structural_rules
                ),
                "effect_meanings": rule_effect_meanings,
                "empty_effect_meanings": empty_rule_effect_meanings,
                "missing_response_rows": missing_rule_response_rows,
                "extra_response_rows": extra_rule_response_rows,
                "unexpected_target_calls": unexpected_rule_targets,
                "duplicate_target_calls": duplicate_rule_targets,
            },
            "table": {
                "outputs": len(tables),
                "empty_meaning": sum(
                    all(_blank(value) for value in item["slots"].values())
                    for item in tables
                ),
                "slots": sum(len(item["slots"]) for item in tables),
                "empty_slots": sum(
                    _blank(value)
                    for item in tables
                    for value in item["slots"].values()
                ),
            },
            "column": {
                "outputs": len(columns),
                "empty_meaning": sum(
                    all(_blank(value) for value in item["slots"].values())
                    for item in columns
                ),
                "slots": sum(len(item["slots"]) for item in columns),
                "empty_slots": sum(
                    _blank(value)
                    for item in columns
                    for value in item["slots"].values()
                ),
            },
            "finding": {
                "rows": finding_rows,
                "summary": findings_summary,
                "rows_file_present": findings_path.exists(),
                "summary_file_present": findings_summary_path.exists(),
            },
        },
        target_table_ids,
        target_column_ids,
    )


def _rule_evidence(run_dir: Path) -> dict[str, dict[str, Any]]:
    audit_records = load_rule_audit_records(run_dir)
    if not (run_dir / RULE_AUDIT_DIRECTORY).is_dir():
        raise ValueError(
            f"current rule structure directory is missing: {run_dir / RULE_AUDIT_DIRECTORY}"
        )
    return {
        f"{target_id}::R-{str(rule_id).removeprefix('R')}": dict(row)
        for target_id, record in audit_records.items()
        for rule_id, row in (record["evidence"].get("rows") or {}).items()
    }


def _split_coordinate_groups(
    groups: list[dict[str, Any]],
    parent_edges: Iterable[tuple[str, str]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Separate real coordinate collisions from nested same-line AST nodes."""
    parent_by_child = {str(child): str(parent) for parent, child in parent_edges}

    def ancestor(left: str, right: str) -> bool:
        seen: set[str] = set()
        current = parent_by_child.get(right)
        while current and current not in seen:
            if current == left:
                return True
            seen.add(current)
            current = parent_by_child.get(current)
        return False

    collisions: list[dict[str, Any]] = []
    nested: list[dict[str, Any]] = []
    for group in groups:
        ids = [str(item.get("id") or "") for item in group.get("nodes", ())]
        is_chain = all(
            ancestor(left, right) or ancestor(right, left)
            for index, left in enumerate(ids)
            for right in ids[index + 1 :]
        )
        (nested if is_chain else collisions).append(group)
    return collisions, nested


def _graph_inventory(
    database: str,
    env_file: Path,
    analysis_scope_id: str,
    generation_id: str,
    target_table_ids: set[str],
    target_column_ids: set[str],
    rule_evidence: dict[str, dict[str, Any]],
) -> dict[str, Any]:
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
            summaries = [
                record["props"]
                for record in session.run(
                    "MATCH (n:CodeNode {analysis_scope_id:$analysis_scope_id, "
                    "generation_id:$generation_id}) "
                    "WHERE n.meaning_status IS NOT NULL "
                    "RETURN properties(n) AS props"
                )
            ]
            rules = [
                record["props"]
                for record in session.run(
                    "MATCH (n:Rule {analysis_scope_id:$analysis_scope_id, "
                    "generation_id:$generation_id}) RETURN properties(n) AS props"
                )
            ]
            examples = [
                record["props"]
                for record in session.run(
                    "MATCH (n:Example {analysis_scope_id:$analysis_scope_id, "
                    "generation_id:$generation_id}) RETURN properties(n) AS props"
                )
            ]
            tables = [
                record["props"]
                for record in session.run(
                    "MATCH (n:Table {analysis_scope_id:$analysis_scope_id, "
                    "generation_id:$generation_id}) RETURN properties(n) AS props"
                )
            ]
            columns = [
                record["props"]
                for record in session.run(
                    "MATCH (n:Column {analysis_scope_id:$analysis_scope_id, "
                    "generation_id:$generation_id}) RETURN properties(n) AS props"
                )
            ]
            duplicate_coordinates = [
                dict(record)
                for record in session.run(
                    "MATCH (n:CodeNode {analysis_scope_id:$analysis_scope_id, "
                    "generation_id:$generation_id}) "
                    "WHERE n.meaning_status IS NOT NULL "
                    "WITH n.file_path AS file, n.start_line AS start, n.end_line AS end, "
                    "collect({id:n.stable_id,summary:n.summary,stereotype:n.stereotype}) AS nodes "
                    "WHERE size(nodes)>1 RETURN file,start,end,nodes ORDER BY file,start,end"
                )
            ]
            parent_edges = [
                (str(record["parent_id"]), str(record["child_id"]))
                for record in session.run(
                    "MATCH (p:CodeNode {analysis_scope_id:$analysis_scope_id, "
                    "generation_id:$generation_id})-[:PARENT_OF]->"
                    "(c:CodeNode {analysis_scope_id:$analysis_scope_id, "
                    "generation_id:$generation_id}) "
                    "RETURN p.stable_id AS parent_id,c.stable_id AS child_id"
                )
            ]
    finally:
        driver.close()

    empty_structural_effect_rules = [
        item for item in rules if _blank(item.get("structural_effects"))
    ]
    structural_effect_classes: Counter[str] = Counter()
    for item in empty_structural_effect_rules:
        row = rule_evidence.get(str(item.get("stable_id") or ""))
        if row is None:
            structural_effect_classes["evidence_missing"] += 1
        elif row.get("direct"):
            structural_effect_classes["direct_nonempty_but_effects_empty"] += 1
        elif row.get("nested_control"):
            structural_effect_classes["nested_control_without_direct"] += 1
        else:
            structural_effect_classes["no_direct_not_nested"] += 1

    def semantic_nodes(items: list[dict[str, Any]], targets: set[str]) -> dict[str, Any]:
        selected = [
            item for item in items
            if str(item.get("stable_id") or "") in targets
        ]
        skipped = [
            item for item in items
            if str(item.get("stable_id") or "") not in targets
        ]
        return {
            "total": len(items),
            "prompt_targets": len(selected),
            "non_targets": len(skipped),
            "target_empty_meaning": sum(
                _blank(item.get("logical_name"))
                and _blank(item.get("analyzed_description"))
                for item in selected
            ),
            "non_target_empty_meaning": sum(
                _blank(item.get("logical_name"))
                and _blank(item.get("analyzed_description"))
                for item in skipped
            ),
            "status": dict(
                Counter(str(item.get("meaning_status", "<absent>")) for item in items)
            ),
            "missing_context_nonempty": sum(
                int(item.get("missing_context_count") or 0) > 0 for item in items
            ),
        }

    duplicate_coordinates, nested_same_line_coordinates = _split_coordinate_groups(
        duplicate_coordinates, parent_edges,
    )

    return {
        "summary": {
            "total": len(summaries),
            "empty_summary": sum(_blank(item.get("summary")) for item in summaries),
            "empty_stereotype": sum(_blank(item.get("stereotype")) for item in summaries),
            "status": dict(
                Counter(str(item.get("meaning_status", "<absent>")) for item in summaries)
            ),
            "missing_context_nonempty": sum(
                int(item.get("missing_context_count") or 0) > 0 for item in summaries
            ),
        },
        "rule": {
            "total": len(rules),
            "empty_structural_condition": sum(
                _blank(item.get("structural_condition")) for item in rules
            ),
            "empty_structural_effects": len(empty_structural_effect_rules),
            "empty_structural_effects_evidence_class": dict(
                structural_effect_classes
            ),
            "structural_effects_without_business_effects": sum(
                not _blank(item.get("structural_effects"))
                and _blank(item.get("business_effects"))
                for item in rules
            ),
        },
        "example": {
            "total": len(examples),
            "empty_given_conditions": sum(
                _blank(item.get("given_conditions")) for item in examples
            ),
            "empty_expected_effects": sum(
                _blank(item.get("expected_effects")) for item in examples
            ),
        },
        "table": semantic_nodes(tables, target_table_ids),
        "column": semantic_nodes(columns, target_column_ids),
        "duplicate_summary_coordinates": {
            "groups": len(duplicate_coordinates),
            "nodes": sum(len(item["nodes"]) for item in duplicate_coordinates),
            "items": duplicate_coordinates,
        },
        "nested_same_line_summary_coordinates": {
            "groups": len(nested_same_line_coordinates),
            "nodes": sum(
                len(item["nodes"]) for item in nested_same_line_coordinates
            ),
            "items": nested_same_line_coordinates,
        },
    }


def main() -> int:
    args = _args()
    run_dir = args.run_dir.resolve()
    prompts, table_ids, column_ids = _prompt_inventory(run_dir)
    rule_evidence = _rule_evidence(run_dir)
    analysis_scope_id, generation_id = _run_identity(run_dir, args.database)
    result = {
        "run_dir": str(run_dir),
        "database": args.database,
        "analysis_scope_id": analysis_scope_id,
        "generation_id": generation_id,
        "semantic_judgment": False,
        "prompt_outputs": prompts,
        "graph": _graph_inventory(
            args.database,
            args.env_file,
            analysis_scope_id,
            generation_id,
            table_ids,
            column_ids,
            rule_evidence,
        ),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
