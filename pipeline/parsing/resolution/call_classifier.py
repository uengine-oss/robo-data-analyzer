"""IR 2.x call classification through semantic adapters and one common core."""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
import fnmatch
from typing import Any, Iterable

from graph.schema.graph_edges import Calls
from graph.schema.graph_nodes import CodeNode
from pipeline.models.call_resolution import build_call_resolution_ledger
from pipeline.parsing.resolution.call_resolution import (
    ResolutionInvariantError,
    resolve_call_sites,
)


@dataclass
class CallClassifyResult:
    edges: list[Calls] = field(default_factory=list)
    ledger: list[dict[str, Any]] = field(default_factory=list)
    resolution_counts: dict[str, int] = field(default_factory=dict)
    unresolved: list[dict[str, str]] = field(default_factory=list)
    dropped_stdlib: int = 0
    dropped_framework: int = 0
    dropped_macro: int = 0
    preserved_inactive: int = 0
    unresolved_presence: int = 0
    unresolved_expression: int = 0
    missing_routine_owner_count: int = 0
    unresolved_preprocessing: int = 0
    unresolved_macro: int = 0
    wrapper_unresolved: int = 0

    @property
    def accounted(self) -> int:
        return len(self.ledger)


class CallClassificationInvariantError(RuntimeError):
    """Canonical call occurrence/accounting or target resolution invariant failed."""


def classify_call_sites(
    call_sites: list[dict],
    code_nodes: list[CodeNode],
    framework_names: frozenset[str] = frozenset(),
    wrappers: Iterable[dict] = (),
    macro_facts: Iterable[dict] = (),
    preprocessing_contexts: Iterable[dict] = (),
    *,
    callable_facts: Iterable[dict] = (),
    parameter_facts: Iterable[dict] = (),
    binding_target_facts: Iterable[dict] = (),
    scope_directive_facts: Iterable[dict] = (),
    import_refs: Iterable[dict] = (),
    evidence_sources: Iterable[dict] = (),
    evidence_completeness: Iterable[dict] = (),
) -> CallClassifyResult:
    """Resolve every canonical call exactly once without name-only fallback.

    Definition targets come only from structural callable facts already projected to
    exact CodeNode IDs. Verified framework contract patterns may only exclude names
    that do not collide with a workspace routine definition.
    """
    framework_patterns = _validated_framework_patterns(
        framework_names, code_nodes,
    )
    wrapper_contracts = _validated_wrapper_contracts(wrappers, code_nodes)
    sites = [dict(item) for item in call_sites]
    _mark_preprocessing_affected_calls(sites, preprocessing_contexts)
    _mark_macro_affected_calls(sites, macro_facts)
    try:
        resolved = resolve_call_sites(
            sites,
            list(callable_facts),
            list(parameter_facts),
            list(binding_target_facts),
            list(scope_directive_facts),
            list(import_refs),
            code_nodes,
            evidence_sources=evidence_sources,
            evidence_completeness=evidence_completeness,
            wrapper_contracts=wrapper_contracts,
            framework_function_patterns=framework_patterns,
        )
        ledger = build_call_resolution_ledger(sites, resolved.outcomes)
    except (ResolutionInvariantError, ValueError) as error:
        raise CallClassificationInvariantError(str(error)) from error

    reasons = Counter(str(row.get("reason") or "") for row in ledger)
    counts = Counter(str(row["resolution_status"]) for row in ledger)
    result = CallClassifyResult(
        edges=resolved.edges,
        ledger=ledger,
        resolution_counts=dict(counts),
        unresolved=[
            {"call_id": str(row["call_id"]), "reason": str(row["reason"])}
            for row in ledger if row["outcome"] == "unresolved"
        ],
        preserved_inactive=counts["inactive"],
        dropped_stdlib=counts["standard_library"],
        dropped_framework=counts["framework_provided"],
        unresolved_presence=reasons["insufficient_conditional_presence"],
        unresolved_expression=reasons["c_runtime_callable_expression"],
        missing_routine_owner_count=reasons["insufficient_routine_owner"],
        unresolved_preprocessing=sum(
            count for reason, count in reasons.items()
            if "preprocessing" in reason or "configuration" in reason
        ),
        unresolved_macro=reasons["insufficient_macro_expansion_state"]
        + reasons["insufficient_conditional_macro_definition"],
        wrapper_unresolved=sum(
            count for reason, count in reasons.items()
            if reason.startswith("runtime_wrapper_")
        ),
    )
    if result.accounted != len(call_sites):
        raise CallClassificationInvariantError(
            "call occurrence result ledger must preserve every canonical ID exactly once"
        )
    return result


def _validated_wrapper_contracts(
    wrappers: Iterable[dict], code_nodes: Iterable[CodeNode],
) -> dict[str, str]:
    result: dict[str, str] = {}
    for item in wrappers:
        wrapper = str(
            item.get("wrapper") if isinstance(item, dict)
            else getattr(item, "wrapper", "")
        ).strip()
        callee_from = str(
            item.get("callee_from") if isinstance(item, dict)
            else getattr(item, "callee_from", "")
        ).strip()
        if (
            not wrapper
            or callee_from not in {"first_arg_string", "first_arg_identifier"}
        ):
            raise CallClassificationInvariantError(
                "runtime wrapper contract requires nonblank wrapper and supported callee_from"
            )
        if wrapper in result:
            raise CallClassificationInvariantError(
                f"duplicate runtime wrapper contract: {wrapper!r}"
            )
        result[wrapper] = callee_from
    routine_names = {
        str(getattr(node, "name", "") or "").strip()
        for node in code_nodes
        if str(getattr(node, "label", "") or "")
        in {"FUNCTION", "METHOD", "PROCEDURE", "CONSTRUCTOR"}
    }
    conflict = next((name for name in result if name in routine_names), None)
    if conflict is not None:
        raise CallClassificationInvariantError(
            "runtime wrapper contract conflicts with workspace routine: "
            f"{conflict!r}"
        )
    return result


def _validated_framework_patterns(
    patterns: Iterable[str], code_nodes: Iterable[CodeNode],
) -> tuple[str, ...]:
    normalized = tuple(sorted({str(pattern).strip() for pattern in patterns if str(pattern).strip()}))
    routine_names = sorted({
        str(getattr(node, "name", "") or "").strip()
        for node in code_nodes
        if str(getattr(node, "label", "") or "")
        in {"FUNCTION", "METHOD", "PROCEDURE", "CONSTRUCTOR"}
        and str(getattr(node, "name", "") or "").strip()
    })
    conflicts = [
        (pattern, name)
        for pattern in normalized
        for name in routine_names
        if fnmatch.fnmatchcase(name, pattern)
    ]
    if conflicts:
        pattern, name = conflicts[0]
        raise CallClassificationInvariantError(
            "framework runtime contract conflicts with workspace routine: "
            f"{pattern!r}->{name!r}"
        )
    return normalized


def _mark_macro_affected_calls(
    call_sites: list[dict], macro_facts: Iterable[dict],
) -> None:
    grouped: dict[tuple[str, str], list[dict]] = {}
    seen: set[str] = set()
    for fact in macro_facts:
        if not isinstance(fact, dict):
            raise CallClassificationInvariantError("macro fact object required")
        fact_id = str(fact.get("fact_id") or "")
        if not fact_id or fact_id in seen:
            raise CallClassificationInvariantError(
                "macro evidence requires unique nonblank canonical fact_id"
            )
        seen.add(fact_id)
        if fact.get("macro_kind") == "function" and fact.get("presence_status") != "inactive":
            key = (str(fact.get("source_id") or ""), str(fact.get("terminal_name") or ""))
            grouped.setdefault(key, []).append(fact)
    for call in call_sites:
        candidates = grouped.get((
            str(call.get("source_id") or ""), str(call.get("call_text") or ""),
        ), ())
        if candidates:
            call["macro_resolution_reason"] = (
                "insufficient_conditional_macro_definition"
                if any(
                    fact.get("presence_status") in {"conditional", "unknown"}
                    for fact in candidates
                )
                else "insufficient_macro_expansion_state"
            )
            call["macro_fact_ids"] = sorted(str(fact["fact_id"]) for fact in candidates)


def _mark_preprocessing_affected_calls(
    call_sites: list[dict], contexts: Iterable[dict],
) -> None:
    by_source = {
        str(context.get("source_id") or ""): context
        for context in contexts if isinstance(context, dict)
    }
    for call in call_sites:
        if call.get("configured_preprocessing_required") is not True:
            continue
        context = by_source.get(str(call.get("source_id") or ""))
        if context is None:
            continue
        build = context.get("build") or {}
        trace = context.get("trace") or {}
        reasons = list(trace.get("reasons") or ()) or list(build.get("reasons") or ())
        call["preprocessing_resolution_reason"] = str(
            reasons[0] if reasons else "insufficient_configured_preprocessing_trace"
        )
        call["preprocessing_evidence_ids"] = sorted(set(
            str(value) for value in (
                list(build.get("commandOccurrenceIds") or ())
                + list(build.get("unresolvedEvidenceIds") or ())
                + list(trace.get("evidenceIds") or ())
            )
        ))
