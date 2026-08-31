"""Language adapter boundary for IR 2.x structural call evidence."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Protocol, Sequence

from graph.schema.graph_nodes import CodeNode


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


@dataclass(frozen=True)
class SemanticCallableCandidate:
    fact_id: str
    source_id: str
    terminal_name: str
    role: str
    scope_path: tuple[tuple[str, int, int], ...]
    declaration_point: int
    type_compatibility: str
    linkage_visibility: str
    linkage_key: str
    linkage_scope: str
    type_key: str
    code_node_id: str
    presence_status: str


@dataclass(frozen=True)
class LanguageSemanticCandidateSet:
    call_id: str
    adapter_schema: str
    language: str
    terminal_name: str
    source_id: str
    call_start: int
    scope_path: tuple[tuple[str, int, int], ...]
    candidates: tuple[SemanticCallableCandidate, ...]
    external_identity_namespace: str
    terminal_status: str = ""
    terminal_reason: str = ""


class LanguageSemanticAdapter(Protocol):
    def adapt(
        self,
        call: Mapping[str, Any],
        callables: Sequence[Mapping[str, Any]],
        parameter_facts: Sequence[Mapping[str, Any]],
        binding_target_facts: Sequence[Mapping[str, Any]],
        scope_directive_facts: Sequence[Mapping[str, Any]],
        import_refs: Sequence[Mapping[str, Any]],
        code_nodes: Sequence[CodeNode],
    ) -> LanguageSemanticCandidateSet: ...


def range_tuple(value: Mapping[str, Any]) -> tuple[int, int]:
    start = value.get("char_offset", value.get("charOffset"))
    length = value.get("char_length", value.get("charLength"))
    return int(start), int(length)


def scope_identity(values: Sequence[Mapping[str, Any]]) -> tuple[tuple[str, int, int], ...]:
    return tuple(
        (str(value.get("kind") or ""), *range_tuple(value.get("range") or {}))
        for value in values
    )


from pipeline.steps.step5_linking.link_calls.c_semantic_adapter import (  # noqa: E402
    CSemanticAdapter,
)
from pipeline.steps.step5_linking.link_calls.java_semantic_adapter import (  # noqa: E402
    JavaSemanticAdapter,
)
from pipeline.steps.step5_linking.link_calls.oracle_semantic_adapter import (  # noqa: E402
    OracleSemanticAdapter,
)
from pipeline.steps.step5_linking.link_calls.postgresql_semantic_adapter import (  # noqa: E402
    PostgreSqlSemanticAdapter,
)
from pipeline.steps.step5_linking.link_calls.python_semantic_adapter import (  # noqa: E402
    PythonSemanticAdapter,
)


_ADAPTERS: dict[tuple[str, str], LanguageSemanticAdapter] = {
    ("c", "antlr-c/v1"): CSemanticAdapter(),
    ("java", "antlr-java/v1"): JavaSemanticAdapter(),
    ("oracle", "antlr-oracle/v1"): OracleSemanticAdapter(),
    ("postgresql", "antlr-postgresql/v1"): PostgreSqlSemanticAdapter(),
    ("python", "antlr-python/v1"): PythonSemanticAdapter(),
}


def semantic_adapter(language: str, frontend_schema: str) -> LanguageSemanticAdapter | None:
    return _ADAPTERS.get((language, frontend_schema))
