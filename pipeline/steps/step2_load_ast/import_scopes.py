"""Import occurrence accounting and environment-bound cross-module scopes.

Parser evidence owns import syntax. Analyzer never reparses legacy import strings or
guesses a target from a basename, package-looking name, or source-tree suffix. Until an
environment-aware binder publishes authoritative module IDs, every canonical binding is
retained as an explicit excluded/unresolved occurrence and no Pass-2 scope is created.
"""
from __future__ import annotations

from dataclasses import dataclass

from pipeline.models.import_resolution import build_import_resolution_ledger


@dataclass(frozen=True)
class ImportScopePlan:
    scopes: dict[str, object]
    ledger: list[dict]


def build_import_scope_plan(import_refs: list[dict]) -> ImportScopePlan:
    """Account for every entry; only authoritative bindings may become scopes.

    IR 1.1 is syntax-complete but does not contain compiler include search paths,
    Java module/class paths, or Python import-system state. No source-name candidate
    indexes are built. A future provider must supply resolved canonical module IDs and
    environment evidence through an explicit binding contract.
    """
    ledger = build_import_resolution_ledger(import_refs)
    resolved = [row for row in ledger if row["outcome"] == "resolved"]
    if resolved:
        raise ValueError(
            "resolved import entries require an environment-aware scope binder"
        )
    return ImportScopePlan(scopes={}, ledger=ledger)
