"""Single definition of parser-owned Rule obligation rows."""
from __future__ import annotations

from pipeline.checks.branch_inventory import BranchInventory


def required_rule_rows(
    inventory: BranchInventory, *, gate_only: bool = False,
) -> list[int]:
    """Return every branch-table row that must materialize as a Rule."""
    if gate_only:
        return [
            row["line"] for row in inventory.table_rows()
            if row["role"] in ("decision", "complement")
        ]
    return [
        row["line"]
        for row in inventory.table_rows()
        if row["role"] in ("decision", "complement", "entry", "continuation")
        or (row["role"] in ("exit", "handler") and not row.get("in_branch"))
    ]
