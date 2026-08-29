"""Structured, serializable quality findings for untrusted LLM candidates."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Literal


FindingStatus = Literal["excluded", "rejected", "unverified", "unavailable"]


@dataclass(frozen=True)
class AnalysisFinding:
    concern: str
    status: FindingStatus
    target_id: str
    coordinates: tuple[int, ...] = ()
    reasons: tuple[str, ...] = ()
    candidate: dict[str, Any] | None = None
    evidence_rule: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Return a stable JSON-compatible record without inventing fallback values."""
        payload = asdict(self)
        payload["coordinates"] = list(self.coordinates)
        payload["reasons"] = list(self.reasons)
        return payload
