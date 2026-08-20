"""Build deterministic evidence packets for direct semantic verification.

This script never judges meaning and never calls an LLM.  It joins the fixed
high-risk function selection, current graph review packets, canonical schema
selection, and immutable per-concern prompt/parsed-response logs so a human or
Codex can trace:

    prompt input -> parsed LLM response -> normalized graph output

All output files are hash-pinned in ``manifest.json``.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Iterable

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from llm.tracing import load_rule_records
from pipeline.quality_gates.rule_audit_artifact import load_rule_audit_records


CONCERNS = (
    "code_summary",
    "code_table_insights",
    "code_rules_examples",
    "module_summary",
    "table_description",
    "component_naming",
)
FUNCTION_CONCERNS = {
    "code_summary",
    "code_table_insights",
    "code_rules_examples",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Join explicit run, selection, and graph-review inputs into "
            "hash-pinned direct semantic verification packets."
        )
    )
    parser.add_argument(
        "--run-output",
        type=Path,
        required=True,
    )
    parser.add_argument(
        "--function-packets",
        type=Path,
        required=True,
    )
    parser.add_argument(
        "--schema-packets",
        type=Path,
        required=True,
    )
    parser.add_argument(
        "--fixed20-selection",
        type=Path,
        required=True,
    )
    parser.add_argument(
        "--schema-selection",
        type=Path,
        required=True,
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        required=True,
    )
    return parser.parse_args()


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    body = "".join(canonical_json(row) + "\n" for row in rows)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8", newline="\n")
    return {
        "path": str(path),
        "sha256": sha256_bytes(body.encode("utf-8")),
        "bytes": len(body.encode("utf-8")),
        "rows": body.count("\n"),
    }


def record_evidence(concern: str, record: dict[str, Any]) -> dict[str, Any]:
    system_prompt = str(record.get("system_prompt") or "")
    user_content = str(record.get("user_content") or "")
    response = record.get("response") or {}
    parsed = response.get("parsed") if isinstance(response, dict) else None
    raw = response.get("raw") if isinstance(response, dict) else None
    target_id = str(record.get("target_id") or "")
    result = {
        "concern": concern,
        "target_id": target_id,
        "sort_key": str(record.get("sort_key") or ""),
        "prompt": {
            "system_sha256": sha256_bytes(system_prompt.encode("utf-8")),
            "user_sha256": sha256_bytes(user_content.encode("utf-8")),
            "system_chars": len(system_prompt),
            "user_chars": len(user_content),
        },
        "response": {
            "parsed": parsed,
            "raw_sha256": sha256_bytes(str(raw or "").encode("utf-8")),
            "raw_chars": len(str(raw or "")),
        },
        "metadata": record.get("metadata") or {},
    }
    return result


def deterministic_rule_evidence(record: dict[str, Any]) -> dict[str, Any]:
    """Represent LLM-0 Rule structure in the same review-stage envelope."""
    evidence_text = canonical_json(record["evidence"])
    return {
        "concern": "deterministic_rule_structure",
        "target_id": record["target_id"],
        "sort_key": record["target_id"],
        "prompt": {
            "system_sha256": sha256_bytes(b""),
            "user_sha256": sha256_bytes(evidence_text.encode("utf-8")),
            "system_chars": 0,
            "user_chars": len(evidence_text),
        },
        "response": {
            "parsed": {"rules": record["rules"]},
            "raw_sha256": sha256_bytes(b""),
            "raw_chars": 0,
        },
        "metadata": {
            "schema": record["schema"],
            "llm_calls": 0,
        },
    }


def main() -> int:
    args = parse_args()
    run_output = args.run_output.resolve()
    prompt_dir = run_output / "llm-prompts"
    output_dir = args.output_dir.resolve()

    fixed_payload = load_json(args.fixed20_selection.resolve())
    fixed_ids = [str(item["func_id"]) for item in fixed_payload["items"]]
    if len(fixed_ids) != 20 or len(set(fixed_ids)) != 20:
        raise RuntimeError("fixed20 selection must contain 20 unique function IDs")

    function_rows = load_jsonl(args.function_packets.resolve())
    functions = {str(row["func_id"]): row for row in function_rows}
    missing_functions = sorted(set(fixed_ids) - set(functions))
    if missing_functions:
        raise RuntimeError(f"fixed20 function packets missing: {missing_functions}")

    schema_payload = load_json(args.schema_selection.resolve())
    schema_ids = [str(item["node_id"]) for item in schema_payload["items"]]
    if len(schema_ids) != 66 or len(set(schema_ids)) != 66:
        raise RuntimeError("canonical schema selection must contain 66 unique node IDs")
    schema_rows = load_jsonl(args.schema_packets.resolve())
    schemas = {str(row["node_id"]): row for row in schema_rows}
    missing_schema = sorted(set(schema_ids) - set(schemas))
    if missing_schema:
        raise RuntimeError(f"canonical schema packets missing: {missing_schema}")

    concern_rows: dict[str, list[dict[str, Any]]] = {}
    concern_records_by_target: dict[str, dict[str, list[dict[str, Any]]]] = {}
    outputs: dict[str, Any] = {}
    deterministic_rules = load_rule_audit_records(run_output)

    for concern in CONCERNS:
        if concern == "code_rules_examples" and deterministic_rules:
            records = [
                deterministic_rule_evidence(record)
                for record in deterministic_rules.values()
            ]
        else:
            path = prompt_dir / f"{concern}.jsonl"
            records = [
                record_evidence(concern, record)
                for record in load_rule_records(path)
            ]
        concern_rows[concern] = records
        by_target: dict[str, list[dict[str, Any]]] = {}
        for record in records:
            by_target.setdefault(record["target_id"], []).append(record)
        concern_records_by_target[concern] = by_target

        selected_records = records
        if concern in FUNCTION_CONCERNS:
            selected_records = [
                record for record in records if record["target_id"] in fixed_ids
            ]
        outputs[f"stage-{concern}"] = write_jsonl(
            output_dir / "stage-records" / f"{concern}.jsonl",
            selected_records,
        )

    fixed_packets: list[dict[str, Any]] = []
    for func_id in fixed_ids:
        fixed_packets.append(
            {
                "func_id": func_id,
                "graph_packet": functions[func_id],
                "stage_records": {
                    concern: concern_records_by_target[concern].get(func_id, [])
                    for concern in sorted(FUNCTION_CONCERNS)
                },
            }
        )
    outputs["fixed20-functions"] = write_jsonl(
        output_dir / "fixed20-functions.jsonl",
        fixed_packets,
    )
    outputs["canonical66-schema"] = write_jsonl(
        output_dir / "canonical66-schema.jsonl",
        [schemas[node_id] for node_id in schema_ids],
    )

    stage_counts = {
        concern: {
            "all_records": len(concern_rows[concern]),
            "selected_records": outputs[f"stage-{concern}"]["rows"],
            "distinct_targets": len(
                {
                    record["target_id"]
                    for record in concern_rows[concern]
                    if record["target_id"]
                }
            ),
        }
        for concern in CONCERNS
    }
    fixed_stage_coverage = {
        concern: {
            "functions_with_record": sum(
                bool(concern_records_by_target[concern].get(func_id))
                for func_id in fixed_ids
            ),
            "records": sum(
                len(concern_records_by_target[concern].get(func_id, []))
                for func_id in fixed_ids
            ),
        }
        for concern in sorted(FUNCTION_CONCERNS)
    }
    manifest = {
        "schema_version": "1.0-direct-semantic-evidence",
        "purpose": "direct field verification evidence; no LLM judge",
        "run_output": str(run_output),
        "inputs": {
            "fixed20_selection": {
                "path": str(args.fixed20_selection.resolve()),
                "sha256": sha256_file(args.fixed20_selection.resolve()),
            },
            "schema_selection": {
                "path": str(args.schema_selection.resolve()),
                "sha256": sha256_file(args.schema_selection.resolve()),
            },
            "function_packets": {
                "path": str(args.function_packets.resolve()),
                "sha256": sha256_file(args.function_packets.resolve()),
            },
            "schema_packets": {
                "path": str(args.schema_packets.resolve()),
                "sha256": sha256_file(args.schema_packets.resolve()),
            },
        },
        "counts": {
            "fixed_functions": len(fixed_packets),
            "canonical_schema_nodes": len(schema_ids),
            "schema_types": {
                node_type: sum(
                    schemas[node_id]["node_type"] == node_type
                    for node_id in schema_ids
                )
                for node_type in ("FILE", "TABLE", "COLUMN")
            },
        },
        "stage_counts": stage_counts,
        "fixed_stage_coverage": fixed_stage_coverage,
        "outputs": outputs,
    }
    manifest_path = output_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
