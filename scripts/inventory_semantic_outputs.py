"""Read-only inventory of semantic prompt outputs and their stored graph values.

This tool makes no semantic judgment and calls no LLM.  It is intended to expose
silent blanks, sufficiency-state coverage, findings, empty deterministic rule/example
fields, and ambiguous source coordinates before a reanalysis.
"""

from __future__ import annotations

import argparse
from collections import Counter
import json
import os
from pathlib import Path
import re
from typing import Any, Iterable

from dotenv import load_dotenv
from neo4j import GraphDatabase

from pipeline.quality_gates.rule_audit_artifact import load_rule_audit_records


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


def _status_counts(values: Iterable[dict[str, Any]]) -> dict[str, int]:
    return dict(Counter(str(value.get("evidence_status", "<absent>")) for value in values))


def _meaning_payload(value: dict[str, Any]) -> dict[str, Any]:
    """Normalize current nested semantic wire values while retaining flat history."""
    meaning = value.get("meaning")
    return meaning if isinstance(meaning, dict) else value


def _prompt_inventory(run_dir: Path) -> tuple[dict[str, Any], set[str], set[str]]:
    prompt_dir = run_dir / "llm-prompts"

    summaries: list[dict[str, Any]] = []
    for path in sorted(prompt_dir.glob("code_summary*.jsonl")):
        for record in _records(path):
            parsed = (record.get("response") or {}).get("parsed") or {}
            if "nodes" in parsed:
                summaries.extend((parsed.get("nodes") or {}).values())
            elif isinstance(parsed.get("result"), dict):
                summaries.append(parsed["result"])
            else:
                summaries.append(parsed)

    audit_records = load_rule_audit_records(run_dir)
    rules: list[dict[str, Any]] = [
        rule
        for record in audit_records.values()
        for rule in record["rules"]
    ]
    if not audit_records:
        for path in sorted(prompt_dir.glob("code_rules_examples*.jsonl")):
            for record in _records(path):
                parsed = (record.get("response") or {}).get("parsed") or {}
                if "nodes" in parsed:
                    for node in (parsed.get("nodes") or {}).values():
                        rules.extend((node.get("rules") or {}).values())
                else:
                    rules.extend((parsed.get("rules") or {}).values())

    tables: list[dict[str, Any]] = []
    columns: list[dict[str, Any]] = []
    target_table_ids: set[str] = set()
    target_column_ids: set[str] = set()
    table_path = prompt_dir / "table_description.jsonl"
    for record in _records(table_path):
        parsed = (record.get("response") or {}).get("parsed") or {}
        table_id = str(record.get("target_id") or "")
        tables.append(_meaning_payload(parsed))
        if table_id:
            target_table_ids.add(table_id)
        for column in parsed.get("columns", []):
            columns.append(_meaning_payload(column))
            name = str(column.get("name") or column.get("column_name") or "")
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
                "status": _status_counts(summaries),
                "missing_context_nonempty": sum(
                    not _blank(item.get("missing_context")) for item in summaries
                ),
            },
            "rule": {
                "outputs": len(rules),
                "empty_nl": sum(_blank(item.get("nl")) for item in rules),
            },
            "table": {
                "outputs": len(tables),
                "empty_meaning": sum(
                    _blank(item.get("logical_name")) and _blank(item.get("description"))
                    for item in tables
                ),
                "status": _status_counts(tables),
                "missing_context_nonempty": sum(
                    not _blank(item.get("missing_context")) for item in tables
                ),
            },
            "column": {
                "outputs": len(columns),
                "empty_meaning": sum(
                    _blank(item.get("logical_name")) and _blank(item.get("description"))
                    for item in columns
                ),
                "logical_name_only": sum(
                    not _blank(item.get("logical_name")) and _blank(item.get("description"))
                    for item in columns
                ),
                "description_only": sum(
                    _blank(item.get("logical_name")) and not _blank(item.get("description"))
                    for item in columns
                ),
                "both_meaning_fields": sum(
                    not _blank(item.get("logical_name")) and not _blank(item.get("description"))
                    for item in columns
                ),
                "status": _status_counts(columns),
                "missing_context_nonempty": sum(
                    not _blank(item.get("missing_context")) for item in columns
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
    if audit_records:
        return {
            f"{target_id}::R-{str(rule_id).removeprefix('R')}": dict(row)
            for target_id, record in audit_records.items()
            for rule_id, row in (record["evidence"].get("rows") or {}).items()
        }
    prompt_dir = run_dir / "llm-prompts"
    decoder = json.JSONDecoder()
    evidence: dict[str, dict[str, Any]] = {}
    paths = list(prompt_dir.glob("code_rules_examples*.jsonl"))
    paths.extend(prompt_dir.glob("code_gate_rules*.jsonl"))
    for path in sorted(set(paths)):
        for record in _records(path):
            content = str(record.get("user_content") or "")
            targets = list(re.finditer(r"^target_id:\s*(.+)$", content, re.MULTILINE))
            if not targets and record.get("target_id") not in (None, "", "unknown"):
                target_id = str(record["target_id"])
                sections = [(target_id, content)]
            else:
                sections = []
                for index, match in enumerate(targets):
                    stop = targets[index + 1].start() if index + 1 < len(targets) else len(content)
                    sections.append((match.group(1).strip(), content[match.end() : stop]))
            for target_id, section in sections:
                marker = section.find("rule_evidence_json:")
                if marker < 0:
                    continue
                payload = section[marker + len("rule_evidence_json:") :].lstrip()
                try:
                    decoded, _ = decoder.raw_decode(payload)
                except json.JSONDecodeError:
                    continue
                for rule_id, row in (decoded.get("rows") or {}).items():
                    evidence[f"{target_id}::R-{str(rule_id).removeprefix('R')}"] = row
    return evidence


def _missing_context_nonempty(value: Any) -> bool:
    if _blank(value):
        return False
    if isinstance(value, str):
        try:
            return not _blank(json.loads(value))
        except json.JSONDecodeError:
            return True
    return True


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
        with driver.session(database=database) as session:
            summaries = [
                record["props"]
                for record in session.run(
                    "MATCH (n) WHERE n.analysis_status IS NOT NULL "
                    "RETURN properties(n) AS props"
                )
            ]
            rules = [
                record["props"]
                for record in session.run("MATCH (n:RULE) RETURN properties(n) AS props")
            ]
            examples = [
                record["props"]
                for record in session.run("MATCH (n:EXAMPLE) RETURN properties(n) AS props")
            ]
            tables = [
                record["props"]
                for record in session.run("MATCH (n:TABLE) RETURN properties(n) AS props")
            ]
            columns = [
                record["props"]
                for record in session.run("MATCH (n:COLUMN) RETURN properties(n) AS props")
            ]
            duplicate_coordinates = [
                dict(record)
                for record in session.run(
                    "MATCH (n) WHERE n.analysis_status IS NOT NULL "
                    "WITH n.file_path AS file, n.start_line AS start, n.end_line AS end, "
                    "collect({id:n.id,summary:n.summary,stereotype:n.stereotype}) AS nodes "
                    "WHERE size(nodes)>1 RETURN file,start,end,nodes ORDER BY file,start,end"
                )
            ]
            parent_edges = [
                (str(record["parent_id"]), str(record["child_id"]))
                for record in session.run(
                    "MATCH (p)-[:PARENT_OF]->(c) "
                    "RETURN p.id AS parent_id,c.id AS child_id"
                )
            ]
    finally:
        driver.close()

    empty_then_rules = [item for item in rules if _blank(item.get("then"))]
    rule_then_classes: Counter[str] = Counter()
    for item in empty_then_rules:
        row = rule_evidence.get(str(item.get("id") or ""))
        if row is None:
            rule_then_classes["evidence_missing"] += 1
        elif row.get("direct"):
            rule_then_classes["direct_nonempty_but_then_empty"] += 1
        elif row.get("nested_control"):
            rule_then_classes["nested_control_without_direct"] += 1
        else:
            rule_then_classes["no_direct_not_nested"] += 1

    def semantic_nodes(items: list[dict[str, Any]], targets: set[str]) -> dict[str, Any]:
        selected = [item for item in items if str(item.get("id") or "") in targets]
        skipped = [item for item in items if str(item.get("id") or "") not in targets]
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
                Counter(str(item.get("semantic_evidence_status", "<absent>")) for item in items)
            ),
            "missing_context_nonempty": sum(
                _missing_context_nonempty(item.get("semantic_missing_context")) for item in items
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
                Counter(str(item.get("summary_evidence_status", "<absent>")) for item in summaries)
            ),
            "missing_context_nonempty": sum(
                _missing_context_nonempty(item.get("summary_missing_context_json"))
                for item in summaries
            ),
        },
        "rule": {
            "total": len(rules),
            "empty_cond": sum(_blank(item.get("cond")) for item in rules),
            "empty_then": len(empty_then_rules),
            "empty_nl": sum(_blank(item.get("nl")) for item in rules),
            "empty_then_evidence_class": dict(rule_then_classes),
        },
        "example": {
            "total": len(examples),
            "empty_given": sum(_blank(item.get("given")) for item in examples),
            "empty_then": sum(_blank(item.get("then")) for item in examples),
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
    result = {
        "run_dir": str(run_dir),
        "database": args.database,
        "semantic_judgment": False,
        "prompt_outputs": prompts,
        "graph": _graph_inventory(
            args.database,
            args.env_file,
            table_ids,
            column_ids,
            rule_evidence,
        ),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
