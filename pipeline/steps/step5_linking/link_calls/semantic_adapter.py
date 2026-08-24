"""Language adapter boundary for IR 2.0 structural call evidence."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Protocol, Sequence

from pipeline.steps.step5_linking.link_calls.c_semantic_adapter import (
    CSemanticAdapter,
)


@dataclass(frozen=True)
class SemanticResolution:
    call_id: str
    status: str
    adapter_schema: str
    reason: str = ""
    target_id: str = ""
    external_identity_key: str = ""
    declaration_fact_id: str = ""
    candidate_fact_ids: tuple[str, ...] = ()


class LanguageSemanticAdapter(Protocol):
    def resolve(
        self,
        call: Mapping[str, Any],
        callables: Sequence[Mapping[str, Any]],
    ) -> SemanticResolution: ...


_ADAPTERS: dict[tuple[str, str], LanguageSemanticAdapter] = {
    ("c", "antlr-c/v1"): CSemanticAdapter(),
}


def semantic_adapter(language: str, frontend_schema: str) -> LanguageSemanticAdapter | None:
    return _ADAPTERS.get((language, frontend_schema))
