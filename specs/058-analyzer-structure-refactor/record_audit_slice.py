"""Record a verified file set in the tab-separated audit ledger."""
from __future__ import annotations

import argparse
import csv
import re
from pathlib import Path


HERE = Path(__file__).resolve().parent
FIELDS = [
    "baseline_path", "final_path", "responsibility", "producers_consumers",
    "maintainability_dry_naming", "dependencies_contracts", "errors_operations",
    "performance_security_generality", "docs_tests", "verdict", "evidence",
]


def _read(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream, delimiter="\t"))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pattern", required=True)
    parser.add_argument("--responsibility", required=True)
    parser.add_argument("--maintainability", required=True)
    parser.add_argument("--contracts", required=True)
    parser.add_argument("--operations", required=True)
    parser.add_argument("--quality", required=True)
    parser.add_argument("--tests", required=True)
    parser.add_argument("--evidence", required=True)
    args = parser.parse_args()

    move_rows = _read(HERE / "move-map.tsv")
    move_map = {row["old_path"]: row for row in move_rows}
    ledger_path = HERE / "audit-ledger.tsv"
    ledger = _read(ledger_path)
    matcher = re.compile(args.pattern)
    changed = 0
    for row in ledger:
        if not matcher.search(row["baseline_path"]):
            continue
        move = move_map.get(row["baseline_path"])
        if move:
            row["final_path"] = move["final_path"]
            users = move["static_importers_sample"]
            row["producers_consumers"] = f"static_importers={move['static_importer_count']}:{users}"
        else:
            row["final_path"] = row["baseline_path"]
            row["producers_consumers"] = "no static Python import; inspected by slice inventory"
        row.update(
            responsibility=args.responsibility,
            maintainability_dry_naming=args.maintainability,
            dependencies_contracts=args.contracts,
            errors_operations=args.operations,
            performance_security_generality=args.quality,
            docs_tests=args.tests,
            verdict="fixed",
            evidence=args.evidence,
        )
        changed += 1
    if not changed:
        raise RuntimeError("pattern matched no baseline rows")
    with ledger_path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=FIELDS, delimiter="\t", lineterminator="\n")
        writer.writeheader()
        writer.writerows(ledger)
    print(f"updated={changed}")


if __name__ == "__main__":
    main()
