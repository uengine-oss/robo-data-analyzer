"""Closed provider boundary for strategy-specific module summaries."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, create_model

from pipeline.languages import module_stereotype_values
from pipeline.models.code_analysis_output import ModuleSummaryOutput


def module_summary_response_model(strategy: str) -> type[BaseModel]:
    """Require every module field and constrain stereotype to its ontology pool."""
    stereotype_type = Literal.__getitem__(module_stereotype_values(strategy))
    fields = ModuleSummaryOutput.model_fields
    return create_model(
        f"ClosedModuleSummaryOutput_{strategy}",
        __base__=ModuleSummaryOutput,
        __config__=ConfigDict(extra="forbid"),
        summary=(
            str,
            Field(..., min_length=1, description=fields["summary"].description),
        ),
        stereotype=(
            stereotype_type,
            Field(..., description=fields["stereotype"].description),
        ),
        logical_name=(
            str,
            Field(..., min_length=1, description=fields["logical_name"].description),
        ),
    )
