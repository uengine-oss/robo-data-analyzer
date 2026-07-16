"""Reconcile audit verdicts against the frozen baseline and final worktree."""
from __future__ import annotations

import csv
import subprocess
from pathlib import Path

from record_audit_slice import FIELDS, _read


HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
BASELINE_COMMIT = "4a1bb126b3fe1c26c0ce54363bb4caab49863486"


def _changed_paths() -> set[str]:
    result = subprocess.run(
        ["git", "-c", "core.quotePath=false", "diff", "--name-only", BASELINE_COMMIT, "--"],
        cwd=REPO,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return {path for path in result.stdout.splitlines() if path}


def main() -> None:
    ledger_path = HERE / "audit-ledger.tsv"
    rows = _read(ledger_path)
    changed = _changed_paths()
    counts = {"fixed": 0, "deleted": 0, "no-violation": 0}

    for row in rows:
        baseline_path = row["baseline_path"]
        final_path = row["final_path"]
        if baseline_path.startswith("NEW:"):
            verdict = "fixed"
        elif not (REPO / final_path).is_file():
            verdict = "deleted"
        elif baseline_path != final_path or final_path in changed:
            verdict = "fixed"
        else:
            verdict = "no-violation"
        row["verdict"] = verdict
        marker = f"verdict={verdict} reconciled against {BASELINE_COMMIT[:8]} and final worktree"
        if marker not in row["evidence"]:
            row["evidence"] = f"{row['evidence']}; {marker}" if row["evidence"] else marker
        counts[verdict] += 1

    with ledger_path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=FIELDS, delimiter="\t", lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    print(" ".join(f"{key}={value}" for key, value in counts.items()))


if __name__ == "__main__":
    main()
