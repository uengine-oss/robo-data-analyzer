"""Verified external-source inventory and semantic records."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


ExternalSourceCategory = Literal[
    "source",
    "sql_xml",
    "config",
    "artifact_xml",
    "document",
    "custom",
    "binary",
    "unknown",
]
ExternalSourceFileStatus = Literal[
    "registered", "analyzed", "unanalyzed", "failed",
]
ExternalRecordProvenance = Literal["source_span", "derived_document"]
ExternalRecordSemanticKind = Literal[
    "xml_element",
    "yaml_entry",
    "json_entry",
    "md_section",
    "record",
    "row",
    "agent_segment",
    "derived_document",
]


def _require_non_negative_integer(value: int, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")


def _require_lines(start_line: int, end_line: int) -> None:
    _require_non_negative_integer(start_line, "source_start_line")
    _require_non_negative_integer(end_line, "source_end_line")
    if (start_line == 0) != (end_line == 0) or (
        start_line and end_line < start_line
    ):
        raise ValueError(
            "source lines must both be zero or form an ordered 1-based range"
        )


def _require_sha256(value: str, name: str) -> None:
    normalized = value.lower()
    if len(normalized) != 64 or any(
        character not in "0123456789abcdef" for character in normalized
    ):
        raise ValueError(f"{name} must be a SHA-256 hex digest")


@dataclass(frozen=True)
class ExternalSourceFile:
    """One inventoried non-code file and its deterministic processing status."""

    source_file_id: str
    project_relative_path: str
    extension: str
    category: ExternalSourceCategory
    status: ExternalSourceFileStatus
    disposition_reason: str = ""

    def __post_init__(self) -> None:
        for name in ("source_file_id", "project_relative_path"):
            if not str(getattr(self, name) or "").strip():
                raise ValueError(f"{name} must be non-empty")
        if self.category not in ExternalSourceCategory.__args__:
            raise ValueError(f"unsupported external source category: {self.category!r}")
        if self.status not in ExternalSourceFileStatus.__args__:
            raise ValueError(f"unsupported external source status: {self.status!r}")
        if self.status in ("unanalyzed", "failed") and not self.disposition_reason.strip():
            raise ValueError(f"status {self.status!r} requires a disposition reason")


@dataclass(frozen=True)
class ExternalSourceRecord:
    """One adapter-produced semantic record whose source identity is re-checkable."""

    source_record_id: str
    source_file_id: str
    external_adapter_id: str
    external_adapter_version: str
    addressable_keys: tuple[str, ...]
    semantic_kind: ExternalRecordSemanticKind
    exact_text: str
    project_relative_path: str
    source_character_start: int
    source_character_end: int
    source_start_line: int
    source_end_line: int
    provenance: ExternalRecordProvenance
    source_sha256: str
    content_sha256: str

    def __post_init__(self) -> None:
        for name in (
            "source_record_id",
            "source_file_id",
            "external_adapter_id",
            "external_adapter_version",
            "project_relative_path",
        ):
            if not str(getattr(self, name) or "").strip():
                raise ValueError(f"{name} must be non-empty")
        if self.semantic_kind not in ExternalRecordSemanticKind.__args__:
            raise ValueError(f"unsupported semantic kind: {self.semantic_kind!r}")
        if self.provenance not in ExternalRecordProvenance.__args__:
            raise ValueError(f"unsupported record provenance: {self.provenance!r}")
        if not self.exact_text:
            raise ValueError("external source record text must be non-empty")
        _require_lines(self.source_start_line, self.source_end_line)
        _require_non_negative_integer(
            self.source_character_start, "source_character_start",
        )
        _require_non_negative_integer(
            self.source_character_end, "source_character_end",
        )
        _require_sha256(self.content_sha256, "content_sha256")
        if self.provenance == "source_span":
            if (
                self.source_character_end - self.source_character_start
                != len(self.exact_text)
            ):
                raise ValueError(
                    "source-span record length must equal its character span width"
                )
            _require_sha256(self.source_sha256, "source_sha256")
        elif self.source_character_start or self.source_character_end:
            raise ValueError(
                "derived-document records cannot claim a source character span"
            )
        if tuple(sorted(set(self.addressable_keys))) != self.addressable_keys:
            raise ValueError("addressable keys must be sorted and unique")
