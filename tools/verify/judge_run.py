"""Audit one sealed Analyzer run without reparsing source or calling a model.

The run artifacts own the input, structural Rule obligations, findings, and verified
product read-back. The active Neo4j generation is checked only against that sealed
identity. This script contains no parser, regex reconstruction, historical prompt
fallback, semantic retry, or second analysis path.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import sys
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from pipeline.quality_gates.rule_audit_artifact import load_rule_audit_records
from scripts.inventory_semantic_outputs import (
    _graph_inventory,
    _prompt_inventory,
    _rule_evidence,
    _run_identity,
)


_RULE_FINDING_STATUSES = frozenset({
    "excluded", "rejected", "unverified", "unavailable",
})


def _json_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"invalid JSON artifact: {path}: {error}") from error
    if not isinstance(value, dict):
        raise ValueError(f"JSON artifact must be an object: {path}")
    return value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_sha256(value: object) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _records(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as error:
                raise ValueError(
                    f"invalid JSONL artifact: {path}:{line_number}: {error}"
                ) from error
            value = value.get("properties", value)
            if not isinstance(value, dict):
                raise ValueError(
                    f"JSONL record must be an object: {path}:{line_number}"
                )
            rows.append(value)
    return rows


def _node_ids(closure: dict[str, Any], product_type: str) -> set[str]:
    prefix = f"node\x1f{product_type}\x1f"
    return {
        str(item.get("identity"))[len(prefix):]
        for item in (closure.get("product_records") or {}).get("identities") or ()
        if str(item.get("identity") or "").startswith(prefix)
    }


def _rule_obligations(run_dir: Path) -> dict[str, dict[str, Any]]:
    obligations: dict[str, dict[str, Any]] = {}
    for target_id, record in load_rule_audit_records(run_dir).items():
        owners = record.get("row_owners") or {}
        rules = {
            f"R{int(rule['anchor_line'])}": dict(rule)
            for rule in record["rules"]
        }
        for row_key in (record["evidence"].get("rows") or {}):
            owner_id = str(owners.get(row_key) or target_id)
            stable_id = f"{owner_id}::R-{str(row_key).removeprefix('R')}"
            if stable_id in obligations:
                raise ValueError(f"duplicate Rule obligation: {stable_id}")
            obligations[stable_id] = rules[str(row_key)]
    return obligations


def _rule_findings(run_dir: Path) -> dict[str, dict[str, Any]]:
    findings: dict[str, dict[str, Any]] = {}
    for row in _records(run_dir / "analysis-findings.jsonl"):
        if row.get("concern") != "rules":
            continue
        status = str(row.get("status") or "")
        target_id = str(row.get("target_id") or "")
        if status not in _RULE_FINDING_STATUSES or "::R-" not in target_id:
            raise ValueError(f"invalid Rule finding: {target_id!r} status={status!r}")
        if target_id in findings:
            raise ValueError(f"duplicate Rule finding: {target_id}")
        findings[target_id] = row
    return findings


def _closure_count_is_exact(closure: dict[str, Any], key: str) -> bool:
    section = closure.get(key)
    return (
        isinstance(section, dict)
        and isinstance(section.get("identities"), list)
        and section.get("count") == len(section["identities"])
    )


def judge(
    run_dir: Path,
    *,
    database: str,
    graph: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return deterministic closure and acceptance accounting for one run."""
    run_dir = run_dir.resolve()
    manifest = _json_object(run_dir / "manifest.json")
    analysis_scope_id, generation_id = _run_identity(run_dir, database)
    sealed_path = run_dir / "sealed-analysis-inputs.json"
    source_closure_path = run_dir / "source-ast-closure.json"
    product_closure_path = run_dir / "product-generation-closure.json"
    sealed = _json_object(sealed_path)
    source_closure = _json_object(source_closure_path)
    product_closure = _json_object(product_closure_path)
    prompt_inventory, _, _ = _prompt_inventory(run_dir)

    obligations = _rule_obligations(run_dir)
    rule_findings = _rule_findings(run_dir)
    published_rules = _node_ids(product_closure, "Rule")
    published_examples = _node_ids(product_closure, "Example")
    unknown_findings = set(rule_findings) - set(obligations)
    expected_published = set(obligations) - set(rule_findings)
    finding_statuses = Counter(
        str(row["status"]) for row in rule_findings.values()
    )

    sealed_ref = manifest.get("sealed_analysis_inputs") or {}
    source_ref = sealed.get("source_ast_closure") or {}
    manifest_product = manifest.get("product_closure") or {}
    sealed_payload = dict(sealed)
    sealed_payload_hash = str(
        sealed_payload.pop("sealed_analysis_inputs_sha256", "") or ""
    )
    checks = {
        "manifest_identity_matches": (
            manifest.get("analysis_scope_id") == analysis_scope_id
            and manifest.get("generation_id") == generation_id
            and product_closure.get("analysis_scope_id") == analysis_scope_id
            and product_closure.get("generation_id") == generation_id
        ),
        "sealed_inputs_hash_matches": (
            bool(sealed_payload_hash)
            and sealed_ref.get("sha256") == sealed_payload_hash
            and _canonical_sha256(sealed_payload) == sealed_payload_hash
        ),
        "source_ast_hash_matches": source_ref.get("sha256") == _sha256(source_closure_path),
        "source_ast_closed": source_closure.get("status") == "exact",
        "product_stage_count_exact": _closure_count_is_exact(
            product_closure, "product_stages"
        ),
        "product_operation_count_exact": _closure_count_is_exact(
            product_closure, "product_operations"
        ),
        "product_record_count_exact": _closure_count_is_exact(
            product_closure, "product_records"
        ),
        "call_occurrence_count_exact": _closure_count_is_exact(
            product_closure, "call_occurrences"
        ),
        "import_occurrence_count_exact": _closure_count_is_exact(
            product_closure, "import_occurrences"
        ),
        "manifest_product_counts_match": all(
            (manifest_product.get(key) or {}).get("count")
            == (product_closure.get(key) or {}).get("count")
            for key in ("product_stages", "product_operations", "product_records")
        ),
        "rule_findings_belong_to_obligations": not unknown_findings,
        "rule_product_exact_once": published_rules == expected_published,
        "rule_response_rows_complete": (
            prompt_inventory["rule"]["missing_response_rows"] == 0
            and prompt_inventory["rule"]["extra_response_rows"] == 0
            and prompt_inventory["rule"]["unexpected_target_calls"] == 0
            and prompt_inventory["rule"]["duplicate_target_calls"] == 0
        ),
        "rule_disposition_accounting": (
            prompt_inventory["rule"]["implementation_detail_rows"]
            == finding_statuses.get("excluded", 0)
            and prompt_inventory["rule"]["business_rule_rows"]
            == len(published_rules)
            + sum(
                count for status, count in finding_statuses.items()
                if status != "excluded"
            )
        ),
        "rule_effect_meanings_nonblank": (
            prompt_inventory["rule"]["empty_effect_meanings"] == 0
        ),
    }
    if graph is not None:
        checks.update({
            "active_graph_rule_count_matches": (
                graph["rule"]["total"] == len(published_rules)
            ),
            "active_graph_example_count_matches": (
                graph["example"]["total"] == len(published_examples)
            ),
            "active_graph_summary_count_matches": (
                graph["summary"]["total"]
                == prompt_inventory["summary"]["outputs"]
            ),
            "active_graph_has_no_duplicate_summary_coordinates": (
                graph["duplicate_summary_coordinates"]["groups"] == 0
            ),
        })

    findings_summary = prompt_inventory["finding"]["summary"] or {}
    blocking_findings = int(
        findings_summary.get("blocking_total", findings_summary.get("total", 0)) or 0
    )
    deterministic_closed = all(checks.values())
    acceptance_ready = (
        deterministic_closed
        and blocking_findings == 0
        and manifest.get("status") == "completed"
        and product_closure.get("quality_status") == "completed"
    )
    return {
        "run_dir": str(run_dir),
        "database": database,
        "analysis_scope_id": analysis_scope_id,
        "generation_id": generation_id,
        "deterministic_closed": deterministic_closed,
        "acceptance_ready": acceptance_ready,
        "checks": checks,
        "counts": {
            "rule_obligations": len(obligations),
            "published_rules": len(published_rules),
            "rule_findings": len(rule_findings),
            "rule_findings_by_status": dict(sorted(finding_statuses.items())),
            "published_examples": len(published_examples),
            "analysis_findings": int(findings_summary.get("total") or 0),
            "blocking_analysis_findings": blocking_findings,
            "call_occurrences": (product_closure.get("call_occurrences") or {}).get("count"),
            "import_occurrences": (product_closure.get("import_occurrences") or {}).get("count"),
            "product_records": (product_closure.get("product_records") or {}).get("count"),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--database")
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    args = parser.parse_args()
    run_dir = args.run_dir.resolve()
    manifest = _json_object(run_dir / "manifest.json")
    database = str(args.database or manifest.get("neo4j_database") or "").strip()
    if not database:
        raise ValueError("run manifest has no Neo4j database")
    _, table_ids, column_ids = _prompt_inventory(run_dir)
    analysis_scope_id, generation_id = _run_identity(run_dir, database)
    graph = _graph_inventory(
        database,
        args.env_file,
        analysis_scope_id,
        generation_id,
        table_ids,
        column_ids,
        _rule_evidence(run_dir),
    )
    result = judge(run_dir, database=database, graph=graph)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["deterministic_closed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
