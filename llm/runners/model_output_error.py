"""Typed boundary for unusable model meaning after a request was delivered.

This is deliberately distinct from transport, budget, metering, evidence-write, and
deterministic invariant failures.  Callers may quarantine the affected semantic target and
continue independent analysis, but must never treat this exception as an empty success or
retry the content.
"""
from __future__ import annotations

from typing import Literal


ModelOutputFailureKind = Literal[
    "repetition", "length", "schema", "stalled", "deadline",
]


class ModelOutputError(RuntimeError):
    """The provider returned or streamed model content that cannot be trusted."""

    def __init__(
        self,
        *,
        rule_name: str,
        kind: ModelOutputFailureKind,
        detail: str,
        original_type: str,
    ) -> None:
        self.rule_name = rule_name
        self.kind = kind
        self.detail = detail
        self.original_type = original_type
        super().__init__(
            f"LLM model output unavailable: rule={rule_name}, kind={kind}, "
            f"error={original_type}: {detail}"
        )


def model_output_kind(error: BaseException) -> ModelOutputFailureKind | None:
    """Classify only failures caused by generated content, never transport/configuration."""
    name = type(error).__name__
    if name == "RepetitionLoopAborted":
        return "repetition"
    if name == "LengthFinishReasonError":
        return "length"
    return None
