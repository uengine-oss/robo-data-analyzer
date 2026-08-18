"""Immutable run evidence for deterministic Rule materialization.

The retired Rule provider used prompt records as both the obligation ledger and the
candidate record.  Deterministic Rules have no provider call, so the run must retain
their parser-owned evidence and exact materialization through a dedicated artifact.
This module owns that on-disk contract for both production writers and audit tools.
"""
from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from graph.schema.graph_nodes import CodeNode
from pipeline.execution.run_output_context import active_run_dir
from pipeline.models.rule_materialization import materialize_rule_items
from pipeline.quality_gates.audit_artifact_paths import audit_file_stem
from pipeline.quality_gates.branch_inventory import BranchInventory
from pipeline.quality_gates.rule_obligations import required_rule_rows
from pipeline.quality_gates.rule_prompt_evidence import rule_evidence


RULE_AUDIT_SCHEMA = "deterministic-rule/v1"
RULE_AUDIT_DIRECTORY = "rule-structures"


def build_rule_audit_record(
    routine: CodeNode,
    inventory: BranchInventory,
    *,
    row_owners: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Build the exact obligation/evidence/materialization record for one routine."""
    rows = required_rule_rows(inventory)
    evidence = rule_evidence(inventory, rows)
    rules = materialize_rule_items(evidence, rows)
    expected_keys = set(evidence.get("rows") or {})
    owners = (
        {row_key: routine.id for row_key in expected_keys}
        if row_owners is None else
        {str(row_key): str(owner).strip() for row_key, owner in row_owners.items()}
    )
    if set(owners) != expected_keys or any(not owner for owner in owners.values()):
        raise ValueError(
            f"{routine.id}: Rule audit row owner coverage mismatch: "
            f"missing={sorted(expected_keys - set(owners))}, "
            f"extra={sorted(set(owners) - expected_keys)}"
        )
    return {
        "schema": RULE_AUDIT_SCHEMA,
        "target_id": routine.id,
        "evidence": evidence,
        "row_owners": owners,
        "rules": [rule.model_dump(mode="json") for rule in rules],
    }


def persist_rule_audit(
    routine: CodeNode,
    inventory: BranchInventory,
    *,
    row_owners: Mapping[str, str] | None = None,
) -> None:
    """Persist one routine record when an analysis run owns an output directory."""
    run_dir = active_run_dir()
    if run_dir is None:
        return
    directory = run_dir / RULE_AUDIT_DIRECTORY
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{audit_file_stem(routine.id)}.json"
    path.write_text(
        json.dumps(
            build_rule_audit_record(routine, inventory, row_owners=row_owners),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )


def load_rule_audit_records(run_dir: Path) -> dict[str, dict[str, Any]]:
    """Load and validate current deterministic Rule artifacts.

    An absent directory means a historical provider-backed run.  Once the directory
    exists, malformed or duplicate records are fatal rather than silently falling
    back to historical prompt parsing.
    """
    directory = run_dir / RULE_AUDIT_DIRECTORY
    if not directory.exists():
        return {}
    if not directory.is_dir():
        raise ValueError(f"Rule audit artifact is not a directory: {directory}")
    records: dict[str, dict[str, Any]] = {}
    for path in sorted(directory.glob("*.json")):
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ValueError(f"invalid Rule audit artifact: {path}: {error}") from error
        record = _validate_record(value, path)
        target_id = record["target_id"]
        if target_id in records:
            raise ValueError(f"duplicate Rule audit target: {target_id}")
        records[target_id] = record
    if not records:
        raise ValueError(f"Rule audit directory contains no records: {directory}")
    return records


def _validate_record(value: object, path: Path) -> dict[str, Any]:
    if not isinstance(value, dict) or value.get("schema") != RULE_AUDIT_SCHEMA:
        raise ValueError(f"invalid Rule audit schema: {path}")
    target_id = str(value.get("target_id") or "").strip()
    evidence = value.get("evidence")
    rules = value.get("rules")
    if not target_id or not isinstance(evidence, Mapping) or not isinstance(rules, list):
        raise ValueError(f"incomplete Rule audit record: {path}")
    evidence_rows = evidence.get("rows")
    if not isinstance(evidence_rows, Mapping):
        raise ValueError(f"Rule audit evidence rows missing: {path}")
    anchors: list[int] = []
    for rule in rules:
        if not isinstance(rule, Mapping):
            raise ValueError(f"Rule audit rule is not an object: {path}")
        try:
            anchors.append(int(rule.get("anchor_line")))
        except (TypeError, ValueError) as error:
            raise ValueError(f"Rule audit anchor is invalid: {path}") from error
    expected = [f"R{anchor}" for anchor in anchors]
    if len(anchors) != len(set(anchors)) or set(evidence_rows) != set(expected):
        raise ValueError(f"Rule audit row coverage mismatch: {path}")
    raw_owners = value.get("row_owners")
    if raw_owners is None:
        # Historical deterministic artifacts predate DBMS child-owner persistence.
        owners = {row_key: target_id for row_key in evidence_rows}
    elif isinstance(raw_owners, Mapping):
        owners = {
            str(row_key): str(owner).strip()
            for row_key, owner in raw_owners.items()
        }
    else:
        raise ValueError(f"Rule audit row_owners is not an object: {path}")
    if set(owners) != set(evidence_rows) or any(not owner for owner in owners.values()):
        raise ValueError(f"Rule audit row owner coverage mismatch: {path}")
    rebuilt = [
        item.model_dump(mode="json")
        for item in materialize_rule_items(evidence, anchors)
    ]
    if rebuilt != [dict(rule) for rule in rules]:
        raise ValueError(f"Rule audit materialization mismatch: {path}")
    return {
        "schema": RULE_AUDIT_SCHEMA,
        "target_id": target_id,
        "evidence": dict(evidence),
        "row_owners": owners,
        "rules": [dict(rule) for rule in rules],
    }
