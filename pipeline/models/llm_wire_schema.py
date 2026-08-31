"""Compact closed models for provider-facing structured responses."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict


def _without_titles(value: Any) -> Any:
    """Remove descriptive JSON-Schema titles that do not constrain a response."""
    if isinstance(value, dict):
        return {
            key: _without_titles(item)
            for key, item in value.items()
            if key != "title"
        }
    if isinstance(value, list):
        return [_without_titles(item) for item in value]
    return value


class CompactLlmResponse(BaseModel):
    """Strict response base whose provider schema contains constraints only."""

    model_config = ConfigDict(extra="forbid")

    @classmethod
    def model_json_schema(cls, *args, **kwargs) -> dict[str, Any]:
        return _without_titles(super().model_json_schema(*args, **kwargs))
