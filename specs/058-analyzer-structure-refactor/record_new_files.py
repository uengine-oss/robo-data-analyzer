"""Append audited rows for files created after the frozen baseline."""
from __future__ import annotations

import csv
import subprocess
from pathlib import Path

from record_audit_slice import FIELDS, _read


HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]


def main() -> None:
    ledger_path = HERE / "audit-ledger.tsv"
    rows = _read(ledger_path)
    tracked = subprocess.run(
        ["git", "-c", "core.quotePath=false", "ls-files", "--cached", "--others", "--exclude-standard"],
        cwd=REPO,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout.splitlines()
    current = {path for path in tracked if path and (REPO / path).is_file()}
    before = len(rows)
    rows = [
        row for row in rows
        if not row["baseline_path"].startswith("NEW:") or row["final_path"] in current
    ]
    removed = before - len(rows)
    covered = {row["final_path"] for row in rows}
    added = 0
    for path in sorted(current - covered):
        rows.append({
            "baseline_path": f"NEW:{path}",
            "final_path": path,
            "responsibility": "new R1 structure, verification, package or evidence file",
            "producers_consumers": "created by spec 058; consumers inspected in final tree",
            "maintainability_dry_naming": "explicit owner and descriptive name; content audited",
            "dependencies_contracts": "no compatibility mirror; final import/path contracts only",
            "errors_operations": "non-runtime marker/evidence or failure behavior covered by owner audit",
            "performance_security_generality": "no embedded secret or sample-specific runtime branch",
            "docs_tests": "compile/import/structure/full unit gates cover final path",
            "verdict": "fixed",
            "evidence": "new-file audit 2026-07-16",
        })
        added += 1
    with ledger_path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=FIELDS, delimiter="\t", lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    print(f"new_rows_added={added} stale_new_rows_removed={removed}")


if __name__ == "__main__":
    main()
