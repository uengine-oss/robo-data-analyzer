"""Language-neutral exact-once resolution core for IR 2.0 call evidence."""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

from graph.schema.graph_edges import Calls
from pipeline.steps.step5_linking.link_calls.semantic_adapter import (
    SemanticResolution,
    semantic_adapter,
)


_STATUSES = frozenset({
    "internal", "external", "dynamic", "configuration_dependent",
    "ambiguous", "unsupported", "inactive",
})


class ResolutionInvariantError(RuntimeError):
    pass


@dataclass(frozen=True)
class ResolutionCoreResult:
    edges: list[Calls]
    outcomes: list[dict[str, Any]]
    counts: Mapping[str, int]


def resolve_call_sites(
    call_sites: Sequence[Mapping[str, Any]],
    callable_facts: Sequence[Mapping[str, Any]],
    *,
    evidence_sources: Iterable[Mapping[str, Any]],
    evidence_completeness: Iterable[Mapping[str, Any]],
    wrapper_names: frozenset[str] = frozenset(),
) -> ResolutionCoreResult:
    sources = _source_index(evidence_sources)
    completeness = _completeness_index(evidence_completeness)
    callable_ids = [str(fact.get("fact_id") or "") for fact in callable_facts]
    if any(not value for value in callable_ids) or len(callable_ids) != len(set(callable_ids)):
        raise ResolutionInvariantError("callable evidence IDs must be globally exact-once")
    call_ids = [str(call.get("call_id") or "") for call in call_sites]
    if any(not value for value in call_ids) or len(call_ids) != len(set(call_ids)):
        raise ResolutionInvariantError("call evidence IDs must be globally exact-once")

    edges: list[Calls] = []
    outcomes: list[dict[str, Any]] = []
    for call in call_sites:
        call_id = str(call["call_id"])
        source_id = str(call.get("source_id") or "")
        source = sources.get(source_id)
        if not call.get("caller_id"):
            resolution = _unsupported(call_id, "insufficient_framework_routine_owner")
        elif source is None:
            resolution = _unsupported(call_id, "missing_evidence_source_identity")
        else:
            resolution = _pre_adapter_resolution(
                call,
                completeness=completeness,
                wrapper_names=wrapper_names,
            )
            if resolution is None:
                adapter = semantic_adapter(
                    str(source.get("language") or ""),
                    str(source.get("frontend_schema") or ""),
                )
                if adapter is None:
                    resolution = _unsupported(call_id, "unsupported_language_adapter")
                else:
                    resolution = adapter.resolve(call, callable_facts)
        _validate_resolution(resolution)
        row: dict[str, Any] = {
            "call_id": call_id,
            "resolution_status": resolution.status,
            "adapter_schema": resolution.adapter_schema,
            "reason": resolution.reason,
            "external_identity_key": resolution.external_identity_key,
            "declaration_fact_id": resolution.declaration_fact_id,
            "candidate_fact_ids": list(resolution.candidate_fact_ids),
            "macro_fact_ids": list(call.get("macro_fact_ids") or ()),
            "preprocessing_evidence_ids": list(
                call.get("preprocessing_evidence_ids") or ()
            ),
        }
        if resolution.status == "internal":
            caller_id = str(call.get("caller_id") or "")
            if not caller_id:
                resolution = _unsupported(call_id, "insufficient_framework_routine_owner")
                row.update({
                    "resolution_status": resolution.status,
                    "adapter_schema": resolution.adapter_schema,
                    "reason": resolution.reason,
                    "external_identity_key": "",
                    "declaration_fact_id": "",
                    "candidate_fact_ids": [],
                    "outcome": "unresolved",
                })
            else:
                edges.append(Calls(
                    caller_id=caller_id,
                    callee_id=resolution.target_id,
                    scope="internal",
                    recursive=caller_id == resolution.target_id,
                ))
                row.update({
                    "outcome": "edge", "scope": "internal",
                    "callee_id": resolution.target_id,
                })
        elif resolution.status in {"external", "inactive"}:
            row["outcome"] = "preserved"
        else:
            row["outcome"] = "unresolved"
        outcomes.append(row)

    outcome_ids = [str(row.get("call_id") or "") for row in outcomes]
    if outcome_ids != call_ids:
        raise ResolutionInvariantError("resolution ledger order/population mismatch")
    counts = Counter(str(row["resolution_status"]) for row in outcomes)
    if sum(counts.values()) != len(call_sites) or set(counts) - _STATUSES:
        raise ResolutionInvariantError("resolution completeness equation does not close")
    return ResolutionCoreResult(edges=edges, outcomes=outcomes, counts=dict(counts))


def _source_index(values: Iterable[Mapping[str, Any]]) -> dict[str, Mapping[str, Any]]:
    result: dict[str, Mapping[str, Any]] = {}
    for value in values:
        source_id = str(value.get("source_id") or "")
        if not source_id or source_id in result:
            raise ResolutionInvariantError("evidence source identity must be exact-once")
        result[source_id] = value
    return result


def _completeness_index(
    values: Iterable[Mapping[str, Any]],
) -> dict[tuple[str, str], Mapping[str, Any]]:
    result: dict[tuple[str, str], Mapping[str, Any]] = {}
    for value in values:
        key = (str(value.get("source_id") or ""), str(value.get("kind") or ""))
        if not all(key) or key in result:
            raise ResolutionInvariantError("evidence completeness rows must be exact-once")
        result[key] = value
    return result


def _pre_adapter_resolution(
    call: Mapping[str, Any],
    *,
    completeness: Mapping[tuple[str, str], Mapping[str, Any]],
    wrapper_names: frozenset[str],
) -> SemanticResolution | None:
    call_id = str(call["call_id"])
    source_id = str(call.get("source_id") or "")
    for kind in ("call", "callable"):
        row = completeness.get((source_id, kind))
        if row is None:
            return _unsupported(call_id, f"missing_{kind}_completeness")
        if row.get("status") != "complete":
            reasons = list(row.get("reasons") or ())
            reason = str(reasons[0] if reasons else row.get("reason") or "")
            if (
                kind == "call"
                and reason == "insufficient_missing_build_configuration"
                and call.get("presence_status") == "active"
            ):
                continue
            status = (
                "configuration_dependent"
                if "configuration" in reason or "preprocessing" in reason
                else "unsupported"
            )
            return SemanticResolution(
                call_id, status, "language-neutral-resolution/v1",
                reason=reason or f"incomplete_{kind}_evidence",
            )
    presence = call.get("presence_status")
    if presence == "inactive":
        return SemanticResolution(
            call_id, "inactive", "language-neutral-resolution/v1",
            reason="inactive_presence",
        )
    if presence in {"conditional", "unknown"}:
        return SemanticResolution(
            call_id, "configuration_dependent", "language-neutral-resolution/v1",
            reason="insufficient_conditional_presence",
        )
    if presence != "active":
        return _unsupported(call_id, "invalid_presence_status")
    if call.get("configured_preprocessing_required") is True:
        return SemanticResolution(
            call_id, "configuration_dependent", "language-neutral-resolution/v1",
            reason=str(
                call.get("preprocessing_resolution_reason")
                or "insufficient_configured_preprocessing_trace"
            ),
        )
    macro_reason = str(call.get("macro_resolution_reason") or "")
    if macro_reason:
        return SemanticResolution(
            call_id, "configuration_dependent", "language-neutral-resolution/v1",
            reason=macro_reason,
            candidate_fact_ids=tuple(str(value) for value in call.get("macro_fact_ids") or ()),
        )
    if str(call.get("call_text") or "") in wrapper_names:
        return SemanticResolution(
            call_id, "dynamic", "language-neutral-resolution/v1",
            reason="runtime_wrapper_target",
        )
    return None


def _unsupported(call_id: str, reason: str) -> SemanticResolution:
    return SemanticResolution(
        call_id, "unsupported", "language-neutral-resolution/v1", reason=reason,
    )


def _validate_resolution(value: SemanticResolution) -> None:
    if not value.call_id or value.status not in _STATUSES or not value.adapter_schema:
        raise ResolutionInvariantError("adapter returned an invalid resolution identity")
    if value.status == "internal":
        if not value.target_id or value.reason or value.external_identity_key:
            raise ResolutionInvariantError("internal resolution contract is incomplete")
    elif value.status == "external":
        if not value.external_identity_key or not value.reason or value.target_id:
            raise ResolutionInvariantError("external resolution contract is incomplete")
    elif not value.reason or value.target_id or value.external_identity_key:
        raise ResolutionInvariantError(f"{value.status} resolution contract is incomplete")
    if len(value.candidate_fact_ids) != len(set(value.candidate_fact_ids)):
        raise ResolutionInvariantError("adapter candidate evidence contains duplicates")
