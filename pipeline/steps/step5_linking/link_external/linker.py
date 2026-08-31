"""Resolve Parser-owned call arguments to verified external source records."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from graph.schema.graph_edges import References
from graph.schema.graph_nodes import Constant
from pipeline.languages import external_resolvers
from pipeline.models.external_reference_resolution import (
    ExternalReferenceOccurrence,
    ExternalReferenceResolutionLedger,
    ResolvedExternalReferenceLink,
)
from pipeline.models.external_source import ExternalSourceFile, ExternalSourceRecord
from pipeline.steps.step5_linking.link_external.literal_constants import (
    LiteralConstantIndex,
    decode_complete_string_literal,
)
from pipeline.steps.step5_linking.link_external.target_index import TargetHit, TargetIndex


class ExternalLinkContractError(ValueError):
    pass


@dataclass(frozen=True)
class LinkOutcome:
    resolved_external_reference_links: list[ResolvedExternalReferenceLink]
    external_reference_ledger: ExternalReferenceResolutionLedger


def link_external(
    *,
    call_sites: Sequence[Mapping[str, Any]],
    constants: list[Constant],
    source_records: list[ExternalSourceRecord],
    source_files: list[ExternalSourceFile] | None = None,
    references_edges: list[References] | None = None,
) -> LinkOutcome:
    """Resolve only configured arguments of exact Parser call occurrences."""
    call_ids = [str(site.get("call_id") or "") for site in call_sites]
    if any(not call_id for call_id in call_ids) or len(call_ids) != len(set(call_ids)):
        raise ExternalLinkContractError(
            "Parser call occurrences require unique non-empty call IDs"
        )
    target_index = TargetIndex(source_records, source_files or [])
    constants_by_routine = LiteralConstantIndex(constants, references_edges)
    resolver_arguments = external_resolvers()
    required_ids: list[str] = []
    occurrences: list[ExternalReferenceOccurrence] = []
    links: list[ResolvedExternalReferenceLink] = []
    seen_links: set[tuple[str, str, str]] = set()

    for site in call_sites:
        resolver_name = str(site.get("call_text") or "")
        argument_indexes = resolver_arguments.get(resolver_name)
        if argument_indexes is None:
            continue
        call_id = str(site["call_id"])
        owner_id = str(site.get("caller_id") or "")
        source_file = str(site.get("file") or "")
        source_line = site.get("line")
        if (
            not source_file
            or isinstance(source_line, bool)
            or not isinstance(source_line, int)
            or source_line < 1
        ):
            raise ExternalLinkContractError(
                f"external resolver call lacks exact source coordinates: {call_id}"
            )
        raw_arguments = site.get("arguments")
        arguments = (
            tuple(str(value.get("source") or "") for value in raw_arguments)
            if isinstance(raw_arguments, (list, tuple))
            and all(isinstance(value, Mapping) for value in raw_arguments)
            else ()
        )
        presence = str(site.get("presence_status") or "active")
        for argument_index in argument_indexes:
            occurrence_id = f"{call_id}:external-argument:{argument_index}"
            required_ids.append(occurrence_id)
            if not owner_id:
                resolution = _unresolved("owner_not_resolved")
            elif presence == "inactive":
                resolution = {
                    "rejected_not_external_reference": {
                        "reason_code": "inactive_call",
                    },
                }
            elif presence != "active":
                resolution = _unresolved("configuration_dependent_call")
            elif argument_index >= len(arguments):
                resolution = _unresolved("missing_argument")
            else:
                argument_source = arguments[argument_index]
                value = _determined_value(
                    argument_source, owner_id, constants_by_routine,
                )
                if value is None:
                    resolution = _unresolved("dynamic_argument")
                else:
                    hits = target_index.lookup(
                        value, source_file=_normalized(source_file),
                    )
                    resolution, linked_hit = _resolve_hits(hits)
                    if linked_hit is not None:
                        link_identity = (
                            owner_id, linked_hit.target_id, linked_hit.matched_key,
                        )
                        if link_identity not in seen_links:
                            seen_links.add(link_identity)
                            links.append(ResolvedExternalReferenceLink(
                                owner_code_node_id=owner_id,
                                external_source_record_id=linked_hit.target_id,
                                external_key=linked_hit.matched_key,
                            ))
            occurrences.append(ExternalReferenceOccurrence(
                external_reference_occurrence_id=occurrence_id,
                parser_call_occurrence_id=call_id,
                owner_code_node_id=owner_id,
                source_file=source_file,
                source_line=source_line,
                resolver_name=resolver_name,
                argument_index=argument_index,
                resolution=resolution,
            ))

    ledger = ExternalReferenceResolutionLedger(
        required_external_reference_occurrence_ids=tuple(required_ids),
        occurrences=tuple(occurrences),
    )
    return LinkOutcome(
        resolved_external_reference_links=links,
        external_reference_ledger=ledger,
    )


def _normalized(path: str) -> str:
    return path.replace("\\", "/").strip().strip("/")


def _determined_value(
    expression: str,
    owner_id: str,
    constants: LiteralConstantIndex,
) -> str | None:
    literal = decode_complete_string_literal(expression)
    if literal is not None:
        return literal
    name = _symbol_name(expression)
    return constants.value_for(owner_id, name) if name else None


def _symbol_name(expression: str) -> str:
    raw = expression.strip()
    parts = raw.split(".")
    if not raw or any(not part.isidentifier() for part in parts):
        return ""
    return parts[-1]


def _unresolved(reason_code: str, candidates: tuple[str, ...] = ()) -> dict:
    return {
        "unresolved": {
            "reason_code": reason_code,
            "candidate_target_ids": candidates,
        },
    }


def _resolve_hits(hits: list[TargetHit]) -> tuple[dict, TargetHit | None]:
    if not hits:
        return _unresolved("target_not_uploaded"), None
    ordered = sorted(
        hits, key=lambda hit: (hit.target_kind, hit.target_id, hit.matched_key),
    )
    if len(ordered) > 1:
        return _unresolved(
            "ambiguous_external_key",
            tuple(hit.target_id for hit in ordered),
        ), None
    hit = ordered[0]
    if hit.target_kind == "external_file":
        return _unresolved(
            "file_target_has_no_verified_record", (hit.target_id,),
        ), None
    return {
        "linked": {
            "external_source_record_id": hit.target_id,
            "external_key": hit.matched_key,
        },
    }, hit
