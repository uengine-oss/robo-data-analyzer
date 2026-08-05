"""Closed wire models for the model-owned semantic header."""
from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, create_model

from pipeline.languages import stereotype_values
from pipeline.models.code_analysis_output import (
    BatchSummaryItem,
    BatchSummaryOutput,
    QuestionItem,
    SummaryOutput,
)


def _fingerprint(value: object) -> str:
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:12]


def _required_wire_fields(strategy: str) -> dict[str, tuple[object, object]]:
    stereotype_type = Literal.__getitem__(stereotype_values(strategy))
    return {
        "summary": (
            str,
            Field(..., min_length=1, description=SummaryOutput.model_fields["summary"].description),
        ),
        "stereotype": (
            stereotype_type,
            Field(..., description=SummaryOutput.model_fields["stereotype"].description),
        ),
        "questions": (
            list[QuestionItem],
            Field(..., max_length=15, description=SummaryOutput.model_fields["questions"].description),
        ),
    }


def summary_response_model(strategy: str) -> type[BaseModel]:
    fingerprint = _fingerprint({"strategy": strategy, "fields": "semantic-header"})
    return create_model(
        f"ClosedSemanticHeader_{fingerprint}",
        __config__=ConfigDict(extra="forbid"),
        **_required_wire_fields(strategy),
    )


def normalize_summary_response(output: BaseModel) -> SummaryOutput:
    values = output.model_dump()
    return SummaryOutput(
        summary=values["summary"],
        stereotype=values["stereotype"],
        questions=values["questions"],
    )


def batch_summary_response_model(node_keys: Iterable[str], strategy: str) -> type[BaseModel]:
    keys = tuple(str(key) for key in node_keys)
    if not keys or any(not key for key in keys) or len(keys) != len(set(keys)):
        raise ValueError("DBMS summary node keys must be non-empty and unique")
    fingerprint = _fingerprint({
        "strategy": strategy,
        "node_keys": keys,
        "fields": "semantic-header",
    })
    item_model = create_model(
        f"ClosedBatchSemanticHeaderItem_{fingerprint}",
        __config__=ConfigDict(extra="forbid"),
        **_required_wire_fields(strategy),
    )
    node_fields = {
        node_key: (item_model, Field(...))
        for node_key in keys
    }
    nodes_model = create_model(
        f"ClosedBatchSemanticHeaderMap_{fingerprint}",
        __config__=ConfigDict(extra="forbid"),
        **node_fields,
    )
    return create_model(
        f"ClosedBatchSemanticHeader_{fingerprint}",
        __config__=ConfigDict(extra="forbid"),
        nodes=(nodes_model, Field(...)),
    )


def normalize_batch_summary_response(
    output: BaseModel, node_keys: Iterable[str],
) -> BatchSummaryOutput:
    nodes = []
    for key in node_keys:
        values = getattr(output.nodes, str(key)).model_dump()
        nodes.append(BatchSummaryItem(
            node_key=str(key),
            summary=values["summary"],
            stereotype=values["stereotype"],
            questions=values["questions"],
        ))
    return BatchSummaryOutput(nodes=nodes)
