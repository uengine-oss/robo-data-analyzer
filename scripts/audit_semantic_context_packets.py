"""Audit whether canonical claim evidence reached immutable semantic prompts.

This script makes no semantic correctness decision and calls no LLM.  It only
checks that every numbered evidence line from each gold claim is present in the
target prompt.  Identifier casing, call spelling, source language, and framework
conventions are deliberately outside this deterministic audit.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from llm.tracing import load_rule_records


_TARGET = re.compile(r"(?m)^target_id:\s*(.+?)\s*$")
_NUMBERED_LINE = re.compile(r"^\s*\d+\s*:\s?")


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _evidence_lines(text: str) -> list[str]:
    return [
        stripped
        for line in text.splitlines()
        if (stripped := _NUMBERED_LINE.sub("", line).strip())
    ]


def audit(
    gold_rows: list[dict[str, Any]],
    prompt_records: list[dict[str, Any]],
) -> dict[str, Any]:
    prompts: dict[str, str] = {}
    for record in prompt_records:
        user = str(record.get("user_content") or "")
        match = _TARGET.search(user)
        if match:
            prompts[match.group(1)] = user

    claim_rows: list[dict[str, Any]] = []
    target_rows: list[dict[str, Any]] = []
    evidence_total = evidence_present = 0
    for gold in gold_rows:
        target_id = str(gold["func_id"])
        prompt = prompts.get(target_id, "")
        for claim in gold.get("gold_claims") or []:
            evidence = str(claim.get("evidence_text") or "")
            lines = _evidence_lines(evidence)
            missing = [line for line in lines if line not in prompt]
            evidence_total += len(lines)
            evidence_present += len(lines) - len(missing)
            claim_rows.append(
                {
                    "target_id": target_id,
                    "claim_id": claim.get("claim_id"),
                    "evidence_lines": len(lines),
                    "missing_evidence_lines": missing,
                    "evidence_complete": not missing,
                }
            )

        target_rows.append(
            {
                "target_id": target_id,
                "prompt_found": bool(prompt),
            }
        )

    return {
        "schema_version": "1.0-context-presence-audit",
        "semantic_judgment": False,
        "counts": {
            "targets": len(gold_rows),
            "prompts_found": sum(row["prompt_found"] for row in target_rows),
            "claims": len(claim_rows),
            "claims_with_complete_evidence": sum(
                row["evidence_complete"] for row in claim_rows
            ),
            "evidence_lines": evidence_total,
            "evidence_lines_present": evidence_present,
        },
        "targets": target_rows,
        "claims": claim_rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--gold-packets", type=Path, required=True)
    parser.add_argument("--prompt-log", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    result = audit(
        _read_jsonl(args.gold_packets.resolve()),
        load_rule_records(args.prompt_log.resolve()),
    )
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(json.dumps(result["counts"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
