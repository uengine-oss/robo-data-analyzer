"""Compact closed models for provider-facing structured responses."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict


_NON_CONSTRAINING_ANNOTATIONS = frozenset({"title", "description"})


def _without_non_constraining_annotations(
    value: Any,
    *,
    property_map: bool = False,
) -> Any:
    """Keep validation constraints while removing duplicated prompt prose."""
    if isinstance(value, dict):
        if property_map:
            return {
                key: _without_non_constraining_annotations(item)
                for key, item in value.items()
            }
        return {
            key: _without_non_constraining_annotations(
                item,
                property_map=key == "properties",
            )
            for key, item in value.items()
            if key not in _NON_CONSTRAINING_ANNOTATIONS
        }
    if isinstance(value, list):
        return [_without_non_constraining_annotations(item) for item in value]
    return value


class CompactLlmResponse(BaseModel):
    """Strict response base whose provider schema contains constraints only."""

    model_config = ConfigDict(extra="forbid")

    @classmethod
    def model_json_schema(cls, *args, **kwargs) -> dict[str, Any]:
        return _without_non_constraining_annotations(
            super().model_json_schema(*args, **kwargs)
        )
