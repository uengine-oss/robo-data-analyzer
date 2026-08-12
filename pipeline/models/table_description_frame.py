"""Dynamic semantic-frame boundary for final TABLE and COLUMN meanings."""
from __future__ import annotations

import hashlib
import json
from typing import Literal, Union

from pydantic import BaseModel, ConfigDict, Field, create_model, model_validator

from pipeline.models.code_analysis_output import GroundedColumnDescription, TableDescription
from pipeline.models.semantic_frame import (
    EvidenceFact,
    SemanticFrame,
    SlotSpec,
    StructuralProfile,
    StructuralTarget,
    evidence_id,
    normalize_semantic_frame_response,
    semantic_frame_response_model,
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
            "sample_refs": [],
            "direct_operations": list(evidence.get("direct_operations") or ()),
            "usage_refs": [
                str(item.get("source_id") or "")
                for item in evidence.get("table_usages") or ()
                if item.get("source_id")
            ],
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
                "usage_lines": sorted({
                    int(line)
                    for candidate in candidates
                    for line in candidate.get("evidence_lines") or ()
                    if int(line) > 0
                }),
                "ownership": sorted({
                    str(candidate.get("ownership") or "")
                    for candidate in candidates
                    if candidate.get("ownership")
                }),
            },
        )
    return table_plan, column_plans


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
    evidence: dict,
    table_plan: tuple[StructuralProfile, tuple[SlotSpec, ...]],
    column_plans: dict[str, tuple[StructuralProfile, tuple[SlotSpec, ...]]],
) -> type[BaseModel]:
    table = evidence.get("table") or {}
    fingerprint = hashlib.sha256(
        json.dumps(evidence, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()[:12]
    table_wire = semantic_frame_response_model(table_plan[1])
    column_models = []
    for name, (_profile, specs) in column_plans.items():
        wire = semantic_frame_response_model(specs)
        column_models.append(create_model(
            f"ColumnFrameWire_{fingerprint}_{hashlib.sha256(name.encode()).hexdigest()[:8]}",
            __config__=ConfigDict(extra="forbid"),
            name=(Literal.__getitem__((name,)), Field(...)),
            slots=(wire.model_fields["slots"].annotation, Field(...)),
        ))
    column_type = (
        column_models[0] if len(column_models) == 1
        else Union.__getitem__(tuple(column_models)) if column_models
        else dict[str, object]
    )
    expected = frozenset(column_plans)

    def _exact_columns(self):
        names = [column.name for column in self.columns]
        if len(names) != len(set(names)) or set(names) != expected:
            raise ValueError(f"columns must match DDL allowlist exactly: {sorted(expected)!r}")
        return self

    validator = model_validator(mode="after")(_exact_columns)
    return create_model(
        f"TableFrameWire_{fingerprint}",
        __config__=ConfigDict(populate_by_name=True, extra="forbid"),
        db=(Literal.__getitem__((str(table.get("db") or ""),)), Field(...)),
        schema_=(Literal.__getitem__((str(table.get("schema") or ""),)), Field(..., alias="schema")),
        name=(Literal.__getitem__((str(table.get("name") or ""),)), Field(...)),
        slots=(table_wire.model_fields["slots"].annotation, Field(...)),
        columns=(
            list[column_type],
            Field(..., min_length=len(expected), max_length=len(expected)),
        ),
        __validators__={"_exact_columns": validator},
    )


def normalize_table_description_frame_response(
    output: BaseModel,
    table_plan: tuple[StructuralProfile, tuple[SlotSpec, ...]],
    column_plans: dict[str, tuple[StructuralProfile, tuple[SlotSpec, ...]]],
) -> TableDescription:
    table_frame = normalize_semantic_frame_response(output, *table_plan)
    column_frames = {
        column.name: normalize_semantic_frame_response(
            column, *column_plans[column.name],
        )
        for column in output.columns
    }
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
        db=output.db,
        schema=output.schema_,
        name=output.name,
        **table_values,
        columns=columns,
        semantic_frame=table_frame,
        column_semantic_frames=column_frames,
    )


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
    elif all(slot.status == "insufficient" for slot in slots):
        status = "insufficient"
    else:
        status = "partial"
        if not missing:
            missing = ["one or more semantic slots are incomplete"]
    return {
        "logical_name": display.meaning,
        "description": description.meaning,
        "evidence_status": status,
        "missing_context": missing,
    }
