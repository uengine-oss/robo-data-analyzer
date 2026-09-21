"""Language-neutral exact-once resolution core for IR 2.x call evidence."""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import fnmatch
import hashlib
from typing import Any, Iterable, Mapping, Sequence

from graph.schema.graph_edges import Calls
from graph.schema.graph_nodes import CodeNode, Variable
from pipeline.parsing.adapters.call_adapter import (
    LanguageSemanticCandidateSet,
    SemanticCallableCandidate,
    SemanticResolution,
    semantic_adapter,
)


_STATUSES = frozenset({
    "internal", "external", "dynamic", "configuration_dependent",
    "ambiguous", "unsupported", "inactive", "standard_library",
    "framework_provided",
})


class ResolutionInvariantError(RuntimeError):
    pass


@dataclass(frozen=True)
class ResolutionCoreResult:
    edges: list[Calls]
    outcomes: list[dict[str, Any]]
    counts: Mapping[str, int]


@dataclass(frozen=True)
class PreAdapterOutcome:
    semantic_call: Mapping[str, Any]
    terminal_resolution: SemanticResolution | None = None


def resolve_call_sites(
    call_sites: Sequence[Mapping[str, Any]],
    callable_facts: Sequence[Mapping[str, Any]],
    parameter_facts: Sequence[Mapping[str, Any]] = (),
    binding_target_facts: Sequence[Mapping[str, Any]] = (),
    scope_directive_facts: Sequence[Mapping[str, Any]] = (),
    import_refs: Sequence[Mapping[str, Any]] = (),
    code_nodes: Sequence[CodeNode] = (),
    variables: Sequence[Variable] = (),
    *,
    evidence_sources: Iterable[Mapping[str, Any]],
    evidence_completeness: Iterable[Mapping[str, Any]],
    wrapper_contracts: Mapping[str, str] | None = None,
    framework_function_patterns: tuple[str, ...] = (),
) -> ResolutionCoreResult:
    sources = _source_index(evidence_sources)
    completeness = _completeness_index(evidence_completeness)
    callable_ids = [str(fact.get("fact_id") or "") for fact in callable_facts]
    if any(not value for value in callable_ids) or len(callable_ids) != len(set(callable_ids)):
        raise ResolutionInvariantError("callable evidence IDs must be globally exact-once")
    parameter_ids = [str(fact.get("fact_id") or "") for fact in parameter_facts]
    if any(not value for value in parameter_ids) or len(parameter_ids) != len(set(parameter_ids)):
        raise ResolutionInvariantError("parameter evidence IDs must be globally exact-once")
    binding_target_ids = [
        str(fact.get("fact_id") or "") for fact in binding_target_facts
    ]
    if (
        any(not value for value in binding_target_ids)
        or len(binding_target_ids) != len(set(binding_target_ids))
    ):
        raise ResolutionInvariantError(
            "binding target evidence IDs must be globally exact-once"
        )
    scope_directive_ids = [
        str(fact.get("fact_id") or "") for fact in scope_directive_facts
    ]
    if (
        any(not value for value in scope_directive_ids)
        or len(scope_directive_ids) != len(set(scope_directive_ids))
    ):
        raise ResolutionInvariantError(
            "scope directive evidence IDs must be globally exact-once"
        )
    import_ids = [
        str(ref.get("import_id") or "") for ref in import_refs
        if str(ref.get("import_id") or "")
    ]
    if len(import_ids) != len(set(import_ids)):
        raise ResolutionInvariantError(
            "import evidence IDs must be globally exact-once"
        )
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
            resolution = _unsupported(call_id, "insufficient_routine_owner")
        elif source is None:
            resolution = _unsupported(call_id, "missing_evidence_source_identity")
        else:
            prepared = _prepare_for_adapter(
                call,
                completeness=completeness,
                wrapper_contracts=wrapper_contracts or {},
                framework_function_patterns=framework_function_patterns,
            )
            resolution = prepared.terminal_resolution
            if resolution is None:
                semantic_call = prepared.semantic_call
                adapter = semantic_adapter(
                    str(source.get("language") or ""),
                    str(source.get("frontend_schema") or ""),
                )
                if adapter is None:
                    resolution = _unsupported(call_id, "unsupported_language_adapter")
                else:
                    candidate_set = adapter.adapt(
                        semantic_call, callable_facts, parameter_facts, binding_target_facts,
                        scope_directive_facts, import_refs, code_nodes,
                        variables,
                    )
                    _validate_candidate_set(
                        candidate_set, call_id=call_id,
                        callable_ids=frozenset(callable_ids),
                    )
                    resolution = resolve_candidate_set(candidate_set)
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
                resolution = _unsupported(call_id, "insufficient_routine_owner")
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
        elif resolution.status in {"standard_library", "framework_provided"}:
            row["outcome"] = "excluded"
        else:
            row["outcome"] = "unresolved"
        outcomes.append(row)

    unique_edges: list[Calls] = []
    seen_edge_pairs: set[tuple[str, str]] = set()
    for edge in edges:
        pair = (edge.caller_id, edge.callee_id)
        if pair not in seen_edge_pairs:
            seen_edge_pairs.add(pair)
            unique_edges.append(edge)

    outcome_ids = [str(row.get("call_id") or "") for row in outcomes]
    if outcome_ids != call_ids:
        raise ResolutionInvariantError("resolution ledger order/population mismatch")
    counts = Counter(str(row["resolution_status"]) for row in outcomes)
    if sum(counts.values()) != len(call_sites) or set(counts) - _STATUSES:
        raise ResolutionInvariantError("resolution completeness equation does not close")
    return ResolutionCoreResult(
        edges=unique_edges, outcomes=outcomes, counts=dict(counts),
    )


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


def _prepare_for_adapter(
    call: Mapping[str, Any],
    *,
    completeness: Mapping[tuple[str, str], Mapping[str, Any]],
    wrapper_contracts: Mapping[str, str],
    framework_function_patterns: tuple[str, ...],
) -> PreAdapterOutcome:
    call_id = str(call["call_id"])
    source_id = str(call.get("source_id") or "")
    for kind in ("call", "callable"):
        row = completeness.get((source_id, kind))
        if row is None:
            return PreAdapterOutcome(
                call, _unsupported(call_id, f"missing_{kind}_completeness"),
            )
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
            return PreAdapterOutcome(
                call,
                SemanticResolution(
                    call_id, status, "language-neutral-resolution/v1",
                    reason=reason or f"incomplete_{kind}_evidence",
                ),
            )
    presence = call.get("presence_status")
    if presence == "inactive":
        return PreAdapterOutcome(
            call,
            SemanticResolution(
                call_id, "inactive", "language-neutral-resolution/v1",
                reason="inactive_presence",
            ),
        )
    if presence in {"conditional", "unknown"}:
        return PreAdapterOutcome(
            call,
            SemanticResolution(
                call_id, "configuration_dependent", "language-neutral-resolution/v1",
                reason="insufficient_conditional_presence",
            ),
        )
    if presence != "active":
        return PreAdapterOutcome(
            call, _unsupported(call_id, "invalid_presence_status"),
        )
    if call.get("configured_preprocessing_required") is True:
        return PreAdapterOutcome(
            call,
            SemanticResolution(
                call_id, "configuration_dependent", "language-neutral-resolution/v1",
                reason=str(
                    call.get("preprocessing_resolution_reason")
                    or "insufficient_configured_preprocessing_trace"
                ),
            ),
        )
    macro_reason = str(call.get("macro_resolution_reason") or "")
    if macro_reason:
        return PreAdapterOutcome(
            call,
            SemanticResolution(
                call_id, "configuration_dependent", "language-neutral-resolution/v1",
                reason=macro_reason,
                candidate_fact_ids=tuple(
                    str(value) for value in call.get("macro_fact_ids") or ()
                ),
            ),
        )
    semantic_call: Mapping[str, Any] = call
    wrapper_name = str(call.get("call_text") or "")
    callee_from = wrapper_contracts.get(wrapper_name)
    if callee_from is not None:
        semantic_call, wrapper_error = _runtime_wrapper_target(
            call, callee_from=callee_from,
        )
        if wrapper_error:
            return PreAdapterOutcome(
                call,
                SemanticResolution(
                    call_id, "dynamic", "language-neutral-resolution/v1",
                    reason=wrapper_error,
                ),
            )
    call_name = str(semantic_call.get("call_text") or "")
    if call_name and any(
        fnmatch.fnmatchcase(call_name, pattern)
        for pattern in framework_function_patterns
    ):
        return PreAdapterOutcome(
            semantic_call,
            SemanticResolution(
                call_id, "framework_provided", "language-neutral-resolution/v1",
                reason="verified_framework_runtime_contract",
            ),
        )
    return PreAdapterOutcome(semantic_call)


def _runtime_wrapper_target(
    call: Mapping[str, Any], *, callee_from: str,
) -> tuple[Mapping[str, Any], str]:
    arguments = call.get("arguments")
    if not isinstance(arguments, list) or not arguments:
        return call, "runtime_wrapper_argument_missing"
    first = arguments[0]
    if (
        not isinstance(first, Mapping)
        or set(first) != {
            "range", "source", "syntax_kind", "literal_kind",
            "literal_value", "identifier",
        }
        or not isinstance(first.get("range"), Mapping)
        or not isinstance(first.get("source"), str)
    ):
        return call, "runtime_wrapper_argument_structure_invalid"
    if callee_from == "first_arg_string":
        if (
            first.get("syntax_kind") != "string_literal"
            or first.get("literal_kind") != "string"
            or first.get("identifier") is not None
        ):
            return call, "runtime_wrapper_string_literal_required"
        target = first.get("literal_value")
    elif callee_from == "first_arg_identifier":
        if (
            first.get("syntax_kind") != "identifier"
            or first.get("literal_kind") is not None
            or first.get("literal_value") is not None
        ):
            return call, "runtime_wrapper_identifier_required"
        target = first.get("identifier")
    else:
        raise ResolutionInvariantError(
            f"unsupported runtime wrapper callee_from: {callee_from!r}"
        )
    if not isinstance(target, str) or not target.strip():
        return call, "runtime_wrapper_target_blank"
    semantic_call = dict(call)
    semantic_call.update({
        "call_text": target,
        "callee_kind": "named",
        "callee_source": str(first.get("source") or ""),
        "callee_range": first.get("range"),
        "receiver_range": None,
        "receiver_source": None,
    })
    return semantic_call, ""


def _unsupported(call_id: str, reason: str) -> SemanticResolution:
    return SemanticResolution(
        call_id, "unsupported", "language-neutral-resolution/v1", reason=reason,
    )


def _validate_candidate_set(
    value: LanguageSemanticCandidateSet,
    *,
    call_id: str,
    callable_ids: frozenset[str],
) -> None:
    if (
        value.call_id != call_id
        or not value.adapter_schema
        or not value.language
        or not value.source_id
        or value.call_start < 0
        or not value.external_identity_namespace
    ):
        raise ResolutionInvariantError("adapter candidate identity is incomplete")
    if bool(value.terminal_status) != bool(value.terminal_reason):
        raise ResolutionInvariantError("adapter terminal candidate is incomplete")
    if value.terminal_status and value.candidates:
        raise ResolutionInvariantError("terminal adapter outcome cannot retain candidates")
    candidate_ids = [candidate.fact_id for candidate in value.candidates]
    if (
        any(not candidate_id for candidate_id in candidate_ids)
        or len(candidate_ids) != len(set(candidate_ids))
        or set(candidate_ids) - callable_ids
    ):
        raise ResolutionInvariantError("adapter candidate population is not a callable subset")
    for candidate in value.candidates:
        if (
            not candidate.source_id
            or not candidate.terminal_name
            or candidate.role not in {"declaration", "definition"}
            or candidate.declaration_point < 0
            or candidate.type_compatibility not in {
                "compatible", "incompatible", "unknown", "not_applicable",
            }
            or candidate.linkage_visibility not in {
                "visible", "not_visible", "conditional", "not_applicable",
            }
            or not candidate.linkage_key
            or candidate.linkage_scope not in {"source", "project"}
            or not candidate.type_key
            or not candidate.presence_status
        ):
            raise ResolutionInvariantError("adapter callable candidate is incomplete")


def resolve_candidate_set(value: LanguageSemanticCandidateSet) -> SemanticResolution:
    if value.terminal_status:
        return SemanticResolution(
            value.call_id,
            value.terminal_status,
            value.adapter_schema,
            reason=value.terminal_reason,
        )
    visible = [
        candidate for candidate in value.candidates
        if candidate.terminal_name == value.terminal_name
        and candidate.presence_status == "active"
        and candidate.linkage_visibility == "visible"
        and candidate.type_compatibility != "incompatible"
    ]
    if not visible:
        return _external_resolution(value, "external", "project")
    binders = visible
    linkages = {
        (candidate.linkage_key, candidate.linkage_scope) for candidate in binders
    }
    ids = _candidate_ids(binders)
    if {linkage for linkage, _ in linkages} == {"unsupported_typedef_callable"}:
        return SemanticResolution(
            value.call_id, "unsupported", value.adapter_schema,
            reason=f"unsupported_{value.language}_typedef_callable",
            candidate_fact_ids=ids,
        )
    if len(linkages) != 1:
        return SemanticResolution(
            value.call_id, "ambiguous", value.adapter_schema,
            reason=f"conflicting_{value.language}_linkage",
            candidate_fact_ids=ids,
        )
    linkage_key, linkage_scope = next(iter(linkages))
    type_keys = {candidate.type_key for candidate in binders}
    if len(type_keys) != 1:
        return SemanticResolution(
            value.call_id, "ambiguous", value.adapter_schema,
            reason=f"conflicting_visible_{value.language}_declarations",
            candidate_fact_ids=ids,
        )
    declaration = max(binders, key=lambda candidate: candidate.declaration_point)
    direct = [candidate for candidate in binders if candidate.role == "definition"]
    if direct:
        return _internal_or_ambiguous(value, declaration.fact_id, direct)
    expected_type = next(iter(type_keys))
    definitions = [
        candidate for candidate in value.candidates
        if candidate.role == "definition"
        and candidate.terminal_name == value.terminal_name
        and candidate.presence_status == "active"
        and candidate.linkage_key == linkage_key
        and candidate.linkage_scope == linkage_scope
        and (linkage_scope != "source" or candidate.source_id == value.source_id)
    ]
    compatible = [
        candidate for candidate in definitions if candidate.type_key == expected_type
    ]
    if compatible:
        return _internal_or_ambiguous(value, declaration.fact_id, compatible)
    if definitions:
        return SemanticResolution(
            value.call_id, "unsupported", value.adapter_schema,
            reason=f"incompatible_{value.language}_definition_evidence",
            declaration_fact_id=declaration.fact_id,
            candidate_fact_ids=_candidate_ids(definitions),
        )
    return _external_resolution(
        value, linkage_key, linkage_scope,
        declaration_fact_id=declaration.fact_id,
    )


def _candidate_ids(values: Sequence[SemanticCallableCandidate]) -> tuple[str, ...]:
    return tuple(sorted(candidate.fact_id for candidate in values))


def _internal_or_ambiguous(
    value: LanguageSemanticCandidateSet,
    declaration_fact_id: str,
    definitions: Sequence[SemanticCallableCandidate],
) -> SemanticResolution:
    candidate_ids = _candidate_ids(definitions)
    if len(definitions) > 1:
        return SemanticResolution(
            value.call_id, "ambiguous", value.adapter_schema,
            reason=f"multiple_compatible_{value.language}_definitions",
            declaration_fact_id=declaration_fact_id,
            candidate_fact_ids=candidate_ids,
        )
    definition = definitions[0]
    if not definition.code_node_id:
        return SemanticResolution(
            value.call_id, "unsupported", value.adapter_schema,
            reason="missing_callable_code_node_projection",
            declaration_fact_id=declaration_fact_id,
            candidate_fact_ids=candidate_ids,
        )
    return SemanticResolution(
        value.call_id, "internal", value.adapter_schema,
        target_id=definition.code_node_id,
        declaration_fact_id=declaration_fact_id,
        candidate_fact_ids=candidate_ids,
    )


def _external_resolution(
    value: LanguageSemanticCandidateSet,
    linkage_key: str,
    linkage_scope: str,
    *,
    declaration_fact_id: str = "",
) -> SemanticResolution:
    source_identity = value.source_id if linkage_scope == "source" else ""
    encoded = "\0".join((
        value.external_identity_namespace,
        linkage_key,
        source_identity,
        value.terminal_name,
    ))
    return SemanticResolution(
        value.call_id, "external", value.adapter_schema,
        reason="partial_upload_external_boundary",
        external_identity_key=hashlib.sha256(encoded.encode()).hexdigest(),
        declaration_fact_id=declaration_fact_id,
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
