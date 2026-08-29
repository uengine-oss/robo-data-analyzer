"""Access-specific function-to-table meaning projected from one closed frame."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Iterable, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from graph.schema.node_ids import sql_column_id
from pipeline.execution.semantic_artifacts import persist_product_missing_context
from pipeline.models.function_meaning import MissingContextItem
from pipeline.models.semantic_frame import (
    EvidenceFact,
    SemanticFrame,
    SemanticSlot,
    SlotSpec,
    StructuralProfile,
)


AccessMode = Literal["read", "write"]
FunctionTableUsageMeaningStatus = Literal["sufficient", "insufficient"]


class FunctionTableUsage(BaseModel):
    """One Analyzer-owned `(function, table, access)` result."""

    model_config = ConfigDict(extra="forbid")

    owner_function_id: str = Field(..., min_length=1)
    physical_table_id: str = Field(..., min_length=1)
    access_mode: AccessMode
    used_column_ids: tuple[str, ...] = ()
    data_usage_occurrence_refs: tuple[str, ...] = Field(..., min_length=1)
    function_table_usage_summary: str | None = None
    meaning_status: FunctionTableUsageMeaningStatus
    missing_context_count: int = Field(..., ge=0)
    missing_context_artifact_hash: str = ""

    @model_validator(mode="after")
    def _coupled_presence(self) -> "FunctionTableUsage":
        has_summary = bool((self.function_table_usage_summary or "").strip())
        if self.meaning_status == "sufficient":
            if not has_summary or self.missing_context_count or self.missing_context_artifact_hash:
                raise ValueError("sufficient table usage has invalid meaning presence")
        elif has_summary or self.missing_context_count < 1:
            raise ValueError("insufficient table usage has invalid meaning presence")
        if self.missing_context_count:
            digest = self.missing_context_artifact_hash
            if len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
                raise ValueError("table usage missing-context hash is invalid")
        elif self.missing_context_artifact_hash:
            raise ValueError("table usage without missing context has an artifact hash")
        if tuple(sorted(set(self.used_column_ids))) != self.used_column_ids:
            raise ValueError("used column IDs must be sorted and unique")
        if len(set(self.data_usage_occurrence_refs)) != len(
            self.data_usage_occurrence_refs
        ):
            raise ValueError("table usage occurrence refs must be unique")
        return self


def function_table_usage_identity(
    owner_function_id: str,
    physical_table_id: str,
    access_mode: AccessMode,
) -> str:
    return f"{owner_function_id}\x1f{physical_table_id}\x1f{access_mode}"


@dataclass(frozen=True)
class FunctionTableUsageSlotGroup:
    """Exact occurrence slots assigned to one `(table, access)` result."""

    physical_table_id: str
    access_mode: AccessMode
    slots: tuple[SlotSpec | SemanticSlot, ...]
    facts: tuple[EvidenceFact, ...]


def group_function_table_usage_slots(
    profile: StructuralProfile,
    slot_specs: Iterable[SlotSpec | SemanticSlot],
    *,
    direct_reads: Iterable[str],
    direct_writes: Iterable[str],
) -> tuple[FunctionTableUsageSlotGroup, ...]:
    """Validate and group every direct DATA_OBJECT occurrence exactly once."""
    expected_keys = {
        (str(table_id), "read") for table_id in direct_reads
    } | {
        (str(table_id), "write") for table_id in direct_writes
    }
    if any(not table_id for table_id, _access in expected_keys):
        raise ValueError("direct table usage contains a blank table ID")

    grouped: dict[
        tuple[str, str], list[tuple[SlotSpec | SemanticSlot, EvidenceFact]]
    ] = defaultdict(list)
    seen_slot_ids: set[str] = set()
    for slot in slot_specs:
        if slot.slot_id in seen_slot_ids:
            raise ValueError(f"duplicate table usage slot: {slot.slot_id}")
        seen_slot_ids.add(slot.slot_id)
        data_facts = [
            profile.evidence[reference]
            for reference in slot.evidence_refs
            if profile.evidence[reference].kind == "DATA_OBJECT"
        ]
        if len(data_facts) != 1:
            raise ValueError(
                f"table usage slot must own exactly one data occurrence: {slot.slot_id}"
            )
        fact = data_facts[0]
        table_id = str(fact.attributes.get("table_id") or "")
        raw_access = str(fact.attributes.get("access") or "").upper()
        if raw_access not in {"READ", "WRITE"}:
            raise ValueError(f"table usage occurrence has invalid access: {fact.id}")
        access_mode = raw_access.lower()
        key = table_id, access_mode
        if key not in expected_keys:
            raise ValueError(f"table usage slot owns an unassigned access: {key!r}")
        if fact.attributes.get("resolution") != "exact" or not bool(
            fact.attributes.get("direct")
        ):
            raise ValueError(f"table usage slot is not direct exact evidence: {fact.id}")
        grouped[key].append((slot, fact))

    if set(grouped) != expected_keys:
        missing = sorted(expected_keys - set(grouped))
        raise ValueError(
            "function-table usage frame is incomplete: "
            f"first_missing={missing[0] if missing else None!r}"
        )

    results: list[FunctionTableUsageSlotGroup] = []
    for (table_id, raw_access_mode), members in sorted(grouped.items()):
        fact_ids = [fact.id for _slot, fact in members]
        if len(fact_ids) != len(set(fact_ids)):
            raise ValueError(
                "one data usage occurrence was assigned more than once: "
                f"{(table_id, raw_access_mode)!r}"
            )
        access_mode: AccessMode = raw_access_mode  # type: ignore[assignment]
        results.append(FunctionTableUsageSlotGroup(
            physical_table_id=table_id,
            access_mode=access_mode,
            slots=tuple(slot for slot, _fact in members),
            facts=tuple(fact for _slot, fact in members),
        ))
    return tuple(results)


def project_function_table_usages(
    frame: SemanticFrame,
    *,
    direct_reads: Iterable[str],
    direct_writes: Iterable[str],
) -> tuple[FunctionTableUsage, ...]:
    """Aggregate per-occurrence table slots without merging READ and WRITE."""
    owner_function_id = frame.target.id
    results: list[FunctionTableUsage] = []
    groups = group_function_table_usage_slots(
        frame.profile,
        frame.slots.values(),
        direct_reads=direct_reads,
        direct_writes=direct_writes,
    )
    for group in groups:
        table_id = group.physical_table_id
        access_mode = group.access_mode
        owned = tuple(zip(group.slots, group.facts, strict=True))
        meanings = tuple(dict.fromkeys(
            " ".join(slot.meaning.split())
            for slot, _fact in owned
            if slot.meaning.strip()
        ))
        used_column_ids = tuple(sorted({
            sql_column_id(
                table_id,
                str(column.get("name") or ""),
                bool(column.get("name_quoted", False)),
            )
            for _slot, fact in owned
            for column in fact.attributes.get("qualified_columns") or ()
            if str(column.get("name") or "").strip()
        }))
        occurrence_refs = tuple(
            fact.id for _slot, fact in owned
        )
        missing_items: list[MissingContextItem] = []
        for slot, _fact in owned:
            if slot.status == "sufficient":
                continue
            description = "; ".join(dict.fromkeys(
                " ".join(str(reason).split())
                for reason in slot.missing_context
                if str(reason).strip()
            ))
            if not description:
                raise ValueError(
                    f"non-sufficient table usage slot lacks a reason: {slot.slot_id}"
                )
            missing_items.append(MissingContextItem(
                missing_context_ref=slot.slot_id,
                reason_code=f"table_usage_slot_{slot.status}",
                description=description,
            ))
        if missing_items and meanings:
            raise ValueError(
                "one function-table access cannot mix mapped and insufficient occurrences"
            )
        if not missing_items:
            if not meanings:
                raise ValueError(
                    f"sufficient table usage lacks meaning: {(table_id, access_mode)!r}"
                )
            status: FunctionTableUsageMeaningStatus = "sufficient"
        else:
            status = "insufficient"
        entity_id = function_table_usage_identity(
            owner_function_id, table_id, access_mode,
        )
        artifact_hash = (
            persist_product_missing_context(
                entity_kind="table-usage",
                entity_id=entity_id,
                meaning_status=status,
                items=tuple(missing_items),
            )
            if missing_items else ""
        )
        results.append(FunctionTableUsage(
            owner_function_id=owner_function_id,
            physical_table_id=table_id,
            access_mode=access_mode,
            used_column_ids=used_column_ids,
            data_usage_occurrence_refs=occurrence_refs,
            function_table_usage_summary=" ".join(meanings) if meanings else None,
            meaning_status=status,
            missing_context_count=len(missing_items),
            missing_context_artifact_hash=artifact_hash,
        ))
    return tuple(results)
