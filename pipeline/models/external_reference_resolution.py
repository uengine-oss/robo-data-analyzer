"""Exact-once Analyzer resolution ledger for Parser-owned external references."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Union

from pydantic import BaseModel, ConfigDict, Field, model_validator


@dataclass(frozen=True)
class ResolvedExternalReferenceLink:
    """One deduplicated routine-to-source-record link before publication."""

    owner_code_node_id: str
    external_source_record_id: str
    external_key: str

    def __post_init__(self) -> None:
        for name in (
            "owner_code_node_id", "external_source_record_id", "external_key",
        ):
            if not str(getattr(self, name) or "").strip():
                raise ValueError(f"{name} must be non-empty")


class LinkedExternalReference(BaseModel):
    model_config = ConfigDict(extra="forbid")

    external_source_record_id: str = Field(..., min_length=1)
    external_key: str = Field(..., min_length=1)


class LinkedExternalReferenceResolution(BaseModel):
    model_config = ConfigDict(extra="forbid")

    linked: LinkedExternalReference


class UnresolvedExternalReference(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason_code: Literal[
        "dynamic_argument",
        "missing_argument",
        "owner_not_resolved",
        "configuration_dependent_call",
        "target_not_uploaded",
        "ambiguous_external_key",
        "file_target_has_no_verified_record",
    ]
    candidate_target_ids: tuple[str, ...] = ()


class UnresolvedExternalReferenceResolution(BaseModel):
    model_config = ConfigDict(extra="forbid")

    unresolved: UnresolvedExternalReference


class RejectedExternalReference(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason_code: Literal["inactive_call"]


class RejectedExternalReferenceResolution(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rejected_not_external_reference: RejectedExternalReference


ExternalReferenceResolution = Union[
    LinkedExternalReferenceResolution,
    UnresolvedExternalReferenceResolution,
    RejectedExternalReferenceResolution,
]


class ExternalReferenceOccurrence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    external_reference_occurrence_id: str = Field(..., min_length=1)
    parser_call_occurrence_id: str = Field(..., min_length=1)
    owner_code_node_id: str = ""
    source_file: str = Field(..., min_length=1)
    source_line: int = Field(..., ge=1)
    resolver_name: str = Field(..., min_length=1)
    argument_index: int = Field(..., ge=0)
    resolution: ExternalReferenceResolution


class ExternalReferenceResolutionLedger(BaseModel):
    model_config = ConfigDict(extra="forbid")

    required_external_reference_occurrence_ids: tuple[str, ...]
    occurrences: tuple[ExternalReferenceOccurrence, ...]

    @model_validator(mode="after")
    def _exact_once(self) -> "ExternalReferenceResolutionLedger":
        required = self.required_external_reference_occurrence_ids
        produced = tuple(
            item.external_reference_occurrence_id for item in self.occurrences
        )
        if any(not item for item in required) or len(required) != len(set(required)):
            raise ValueError("external reference required IDs must be unique and non-empty")
        if produced != required:
            raise ValueError(
                "external reference resolution population/order differs from required IDs"
            )
        return self
