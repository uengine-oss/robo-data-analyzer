"""Dynamic semantic-frame boundary for final TABLE and COLUMN meanings."""
from __future__ import annotations

import json
from typing import Literal

from pydantic import BaseModel, Field, create_model

from pipeline.models.code_analysis_output import GroundedColumnDescription, TableDescription
from pipeline.models.llm_wire_schema import CompactLlmResponse
from pipeline.models.semantic_frame import (
    EvidenceFact,
    SemanticFrame,
    SemanticSlot,
    SlotSpec,
    StructuralProfile,
    StructuralTarget,
    evidence_id,
    slot_id,
)


def build_table_description_plans(
    table_id: str,
    evidence: dict,
) -> tuple[
    tuple[StructuralProfile, tuple[SlotSpec, ...]],
    dict[str, tuple[StructuralProfile, tuple[SlotSpec, ...]]],
]:
    table = evidence.get("table") or {}
    table_plan = _entity_plan(
        target_id=table_id,
        family="TABLE",
        owner_id=table_id,
        attributes={
            "table_id": table_id,
            "db": str(table.get("db") or ""),
            "schema": str(table.get("schema") or ""),
            "name": str(table.get("name") or ""),
            "source_description": _source_description(table),
            "observed_operations": _observed_operations(evidence),
        },
    )
    column_plans = {}
    for column in evidence.get("columns") or ():
        name = str(column.get("name") or "")
        column_id = f"{table_id}.{name}"
        candidates = list(column.get("candidates") or ())
        column_plans[name] = _entity_plan(
            target_id=column_id,
            family="COLUMN",
            owner_id=table_id,
            attributes={
                "table_id": table_id,
                "name": name,
                "source_description": _source_description(column),
                "usage_evidence": _column_usage_evidence(candidates),
            },
        )
    return table_plan, column_plans


def _observed_operations(evidence: dict) -> list[dict[str, str]]:
    operations: list[dict[str, str]] = []
    for item in evidence.get("direct_operations") or ():
        operation = {
            "access": str(item.get("relation") or ""),
            "operation": str(item.get("dml_type") or ""),
        }
        if operation not in operations:
            operations.append(operation)
    return operations


def _column_usage_evidence(candidates: list[dict]) -> list[str]:
    usages: list[str] = []
    for candidate in candidates:
        for source in candidate.get("source_lines") or ():
            text = str(source.get("focus_text") or source.get("text") or "").strip()
            if text and text not in usages:
                usages.append(text)
    return usages


def table_description_prompt_values(
    table_id: str,
    table_plan: tuple[StructuralProfile, tuple[SlotSpec, ...]],
    column_plans: dict[str, tuple[StructuralProfile, tuple[SlotSpec, ...]]],
) -> dict[str, str]:
    """Serialize only meaning-bearing table and column facts exactly once."""
    table_profile, _table_specs = table_plan
    packet = {
        "table": _meaning_input(table_profile),
        "columns": {
            name: _meaning_input(profile)
            for name, (profile, _specs) in column_plans.items()
        },
    }
    return {
        # Kept outside the rendered prompt so tracing can locate this call.
        "table_fqn": table_id,
        "table_and_column_meaning_inputs": json.dumps(
            packet,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ),
    }


def _meaning_input(profile: StructuralProfile) -> dict[str, object]:
    """Drop internal identity and keep facts that can change a catalog meaning."""
    facts = [
        fact for fact in profile.evidence.values()
        if fact.kind in {"TABLE", "COLUMN"}
    ]
    if len(facts) != 1:
        raise ValueError("table meaning input requires exactly one entity fact")
    attributes = facts[0].attributes
    allowed = (
        ("source_description", "observed_operations")
        if facts[0].kind == "TABLE"
        else ("source_description", "usage_evidence")
    )
    return {
        key: value
        for key in allowed
        if (value := attributes.get(key)) not in (None, "", [], {})
    }
def _source_description(item: dict) -> str:
    parts = []
    for key in ("source_logical_name", "source_description", "source_comment"):
        value = str(item.get(key) or "").strip()
        if value:
            parts.append(f"{key}:{value}")
    return "\n".join(parts)


def _entity_plan(
    *, target_id: str, family: Literal["TABLE", "COLUMN"],
    owner_id: str, attributes: dict,
) -> tuple[StructuralProfile, tuple[SlotSpec, ...]]:
    target = StructuralTarget(
        id=target_id,
        type=family,
        owner_id=owner_id,
        file="(database-metadata)",
        start_line=0,
        end_line=0,
    )
    target_ref = evidence_id(target_id, "TARGET", 0)
    fact_ref = evidence_id(target_id, family, attributes)
    evidence = {
        target_ref: EvidenceFact(
            id=target_ref,
            kind="TARGET",
            owner_id=target_id,
            attributes={
                "type": family, "signature": "", "name": target_id,
                "params": "", "return_type": "",
            },
        ),
        fact_ref: EvidenceFact(
            id=fact_ref,
            kind=family,
            owner_id=owner_id,
            attributes=attributes,
        ),
    }
    profile = StructuralProfile(target=target, evidence=evidence)
    specs = tuple(
        SlotSpec(
            slot_id=slot_id(target_id, family, role, "TARGET"),
            family=family,
            role=role,
            target_ref=target_ref,
            evidence_refs=(target_ref, fact_ref),
        )
        for role in ("DISPLAY_NAME", "DESCRIPTION")
    )
    return profile, specs


def table_description_frame_response_model(
    column_plans: dict[str, tuple[StructuralProfile, tuple[SlotSpec, ...]]],
) -> type[BaseModel]:
    entity_model = create_model(
        "TableMeaningFields",
        __base__=CompactLlmResponse,
        logical_name=(str, Field(...)),
        description=(str, Field(...)),
    )
    column_fields = {}
    for index, name in enumerate(column_plans):
        field_name = f"column_{index}"
        column_fields[field_name] = (
            entity_model,
            Field(..., alias=name),
        )
    columns_wire = create_model(
        f"ColumnFrameMap_{len(column_fields)}",
        __base__=CompactLlmResponse,
        **column_fields,
    )
    model = create_model(
        "TableFrameWire",
        __base__=CompactLlmResponse,
        logical_name=(str, Field(...)),
        description=(str, Field(...)),
        columns=(columns_wire, Field(...)),
    )
    return model


def normalize_table_description_frame_response(
    output: BaseModel,
    table_plan: tuple[StructuralProfile, tuple[SlotSpec, ...]],
    column_plans: dict[str, tuple[StructuralProfile, tuple[SlotSpec, ...]]],
) -> TableDescription:
    table_frame = _meaning_frame(output, *table_plan)
    column_bindings = {
        name: f"column_{index}"
        for index, name in enumerate(column_plans)
    }
    column_frames = {
        name: _meaning_frame(
            getattr(output.columns, column_bindings[name]), *column_plans[name],
        )
        for name in column_plans
    }
    table_facts = tuple(
        fact for fact in table_plan[0].evidence.values() if fact.kind == "TABLE"
    )
    if len(table_facts) != 1:
        raise ValueError("table semantic plan must contain exactly one TABLE fact")
    table_identity = table_facts[0].attributes
    table_values = _project_entity(table_frame)
    columns = []
    for name in column_plans:
        values = _project_entity(column_frames[name])
        columns.append(GroundedColumnDescription(
            name=name,
            **values,
            semantic_frame=column_frames[name],
        ))
    return TableDescription(
        db=str(table_identity.get("db") or ""),
        schema=str(table_identity.get("schema") or ""),
        name=str(table_identity.get("name") or ""),
        **table_values,
        columns=columns,
        semantic_frame=table_frame,
        column_semantic_frames=column_frames,
    )


def _meaning_frame(
    output: BaseModel,
    profile: StructuralProfile,
    specs: tuple[SlotSpec, ...],
) -> SemanticFrame:
    """Attach the two model-owned meaning fields to Analyzer-owned slot identities."""
    values = {
        "DISPLAY_NAME": str(output.logical_name).strip(),
        "DESCRIPTION": str(output.description).strip(),
    }
    slots = {}
    for spec in specs:
        meaning = values[spec.role]
        slots[spec.slot_id] = SemanticSlot(
            slot_id=spec.slot_id,
            family=spec.family,
            role=spec.role,
            target_ref=spec.target_ref,
            evidence_refs=spec.evidence_refs,
            meaning=meaning,
            status="sufficient" if meaning else "insufficient",
            missing_context=(
                () if meaning else (
                    f"assigned {spec.family}.{spec.role} returned no meaning",
                )
            ),
        )
    return SemanticFrame(target=profile.target, profile=profile, slots=slots)


def _project_entity(frame: SemanticFrame) -> dict:
    by_role = {slot.role: slot for slot in frame.slots.values()}
    display = by_role["DISPLAY_NAME"]
    description = by_role["DESCRIPTION"]
    slots = (display, description)
    missing = list(dict.fromkeys(
        item for slot in slots for item in slot.missing_context
    ))
    if all(slot.status == "sufficient" for slot in slots):
        status = "sufficient"
        logical_name = _sufficient_slot_meaning(
            display,
            expected_role="DISPLAY_NAME",
        )
        analyzed_description = _sufficient_slot_meaning(
            description,
            expected_role="DESCRIPTION",
        )
    else:
        status = "insufficient"
        logical_name = ""
        analyzed_description = ""
        if not missing:
            raise ValueError("incomplete table meaning slots lack missing context")
    return {
        "logical_name": logical_name,
        "description": analyzed_description,
        "evidence_status": status,
        "missing_context": missing,
    }


def _sufficient_slot_meaning(slot, *, expected_role: str) -> str:
    """Project one named semantic obligation without borrowing a sibling field."""
    if slot.role != expected_role or slot.status != "sufficient":
        raise ValueError(
            f"cannot project {expected_role} from {slot.role}/{slot.status}"
        )
    if not isinstance(slot.meaning, str) or not slot.meaning.strip():
        raise ValueError(f"sufficient {expected_role} slot has no meaning")
    return slot.meaning
