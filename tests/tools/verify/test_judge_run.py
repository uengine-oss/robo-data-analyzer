from __future__ import annotations

import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from scripts.judge_run import judge


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value), encoding="utf-8")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _canonical_sha256(value: object) -> str:
    return hashlib.sha256(json.dumps(
        value, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")).hexdigest()


def _run_fixture(root: Path, *, excluded: bool) -> Path:
    run_dir = root / "run"
    prompt_dir = run_dir / "llm-prompts"
    rule_dir = run_dir / "rule-structures"
    prompt_dir.mkdir(parents=True)
    rule_dir.mkdir()
    target_id = "code:order.c:validate"
    rule_id = f"{target_id}::R-1"
    rule = {
        "anchor_line": 1,
        "structural_condition": 'F("ready")',
        "structural_effects": [],
        "business_effects": [],
    }
    _write_json(rule_dir / "validate.json", {
        "schema": "deterministic-rule/v1",
        "target_id": target_id,
        "evidence": {
            "conditions": {"C1": "ready"},
            "effects": {},
            "rows": {"R1": {"role": "decision", "when": ["C1"]}},
        },
        "rules": [rule],
    })
    response = {
        "target_id": target_id,
        "response": {"parsed": {
            "rows": {"R1": (
                "implementation_detail" if excluded else "business_rule"
            )},
            "effects": {},
        }},
    }
    (prompt_dir / "code_rule_meanings.jsonl").write_text(
        json.dumps(response) + "\n", encoding="utf-8",
    )

    findings = []
    if excluded:
        findings.append({
            "concern": "rules",
            "status": "excluded",
            "target_id": rule_id,
            "coordinates": [1],
        })
    (run_dir / "analysis-findings.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in findings), encoding="utf-8",
    )
    status = "completed"
    _write_json(run_dir / "analysis_findings_summary.json", {
        "total": len(findings),
        "blocking_total": 0,
        "by_status": {"excluded": 1} if excluded else {},
        "by_concern": {"rules": 1} if excluded else {},
    })
    source_closure = run_dir / "source-ast-closure.json"
    _write_json(source_closure, {"status": "exact"})
    sealed = run_dir / "sealed-analysis-inputs.json"
    sealed_payload = {
        "source_ast_closure": {"sha256": _sha256(source_closure)},
    }
    sealed_hash = _canonical_sha256(sealed_payload)
    _write_json(sealed, {
        **sealed_payload,
        "sealed_analysis_inputs_sha256": sealed_hash,
    })

    record_identities = [] if excluded else [{
        "identity": f"node\x1fRule\x1f{rule_id}",
        "sha256": "a" * 64,
    }]
    product_closure = {
        "analysis_scope_id": "analysis-scope/v2:test",
        "generation_id": "generation-1",
        "quality_status": status,
        "product_stages": {"count": 0, "identities": []},
        "product_operations": {"count": 0, "identities": []},
        "product_records": {
            "count": len(record_identities), "identities": record_identities,
        },
        "call_occurrences": {"count": 0, "identities": []},
        "import_occurrences": {"count": 0, "identities": []},
    }
    _write_json(run_dir / "product-generation-closure.json", product_closure)
    _write_json(run_dir / "manifest.json", {
        "analysis_scope_id": "analysis-scope/v2:test",
        "generation_id": "generation-1",
        "neo4j_database": "framework",
        "status": status,
        "sealed_analysis_inputs": {"sha256": sealed_hash},
        "product_closure": {
            key: {"count": product_closure[key]["count"]}
            for key in ("product_stages", "product_operations", "product_records")
        },
    })
    return run_dir


class SealedRunJudgeTest(unittest.TestCase):
    def test_exact_rule_ledger_is_acceptance_ready(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            result = judge(
                _run_fixture(Path(temporary_directory), excluded=False),
                database="framework",
            )

        self.assertTrue(result["deterministic_closed"])
        self.assertTrue(result["acceptance_ready"])
        self.assertEqual(1, result["counts"]["published_rules"])

    def test_accounted_exclusion_closes_ledger_and_does_not_block_acceptance(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            result = judge(
                _run_fixture(Path(temporary_directory), excluded=True),
                database="framework",
            )

        self.assertTrue(result["deterministic_closed"])
        self.assertTrue(result["acceptance_ready"])
        self.assertEqual({"excluded": 1}, result["counts"]["rule_findings_by_status"])


if __name__ == "__main__":
    unittest.main()
