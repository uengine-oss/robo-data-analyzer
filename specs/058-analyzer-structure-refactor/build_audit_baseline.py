"""Create the UTF-8 exact audit baseline and initial pending ledger once."""
from __future__ import annotations

import csv
import subprocess
from pathlib import Path


BASELINE = "4a1bb126b3fe1c26c0ce54363bb4caab49863486"
HERE = Path(__file__).resolve().parent


def _responsibility(path: str) -> str:
    if path.startswith("specs/"):
        return "historical-spec"
    if path.startswith(".specify/"):
        return "spec-governance"
    if path.startswith("analysis_flow/"):
        return "pipeline-step"
    if path.startswith("shared/neo4j/"):
        return "graph"
    if path.startswith("shared/llm/"):
        return "llm-client"
    if path.startswith("shared/log/"):
        return "logging"
    if path.startswith("shared/"):
        return "shared-requires-classification"
    if path.startswith("search/"):
        return "search"
    if path.startswith("api/"):
        return "api"
    if path.startswith(("prompts/", "skills/", "llm_configs/", "agent_tools/")):
        return "llm-assets"
    if path.startswith("cli_scripts/"):
        return "scripts"
    if path.startswith("data_models/"):
        return "pipeline-model"
    if path.startswith("deploy/"):
        return "deploy-r1b"
    return "repository-root"


def main() -> None:
    ledger = HERE / "audit-ledger.tsv"
    if ledger.exists():
        with ledger.open(encoding="utf-8", newline="") as stream:
            existing = list(csv.DictReader(stream, delimiter="\t"))
        if any(row.get("verdict") != "pending" for row in existing):
            raise RuntimeError("refusing to overwrite a ledger that already has final verdicts")

    paths = subprocess.check_output(
        ["git", "-c", "core.quotePath=false", "ls-tree", "-r", "--name-only", BASELINE],
        text=True,
        encoding="utf-8",
    ).splitlines()
    baseline = [
        "# Analyzer v10 audit baseline",
        f"# baseline_commit={BASELINE}",
        f"# tracked_file_count={len(paths)}",
        "# Every path below must receive exactly one final audit-ledger verdict.",
        *paths,
    ]
    (HERE / "audit-baseline.txt").write_text("\n".join(baseline) + "\n", encoding="utf-8")

    fields = [
        "baseline_path", "final_path", "responsibility", "producers_consumers",
        "maintainability_dry_naming", "dependencies_contracts", "errors_operations",
        "performance_security_generality", "docs_tests", "verdict", "evidence",
    ]
    with ledger.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, delimiter="\t", lineterminator="\n")
        writer.writeheader()
        for path in paths:
            row = {field: "pending" for field in fields}
            row.update(baseline_path=path, responsibility=_responsibility(path))
            writer.writerow(row)


if __name__ == "__main__":
    main()
