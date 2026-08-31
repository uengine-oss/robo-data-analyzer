"""Provider structured-output binding with immutable schema ownership."""
from __future__ import annotations

import copy
from typing import Any


STRUCTURED_OUTPUT_METHODS = frozenset({
    "provider_default",
    "json_schema",
    "function_calling",
})
STRUCTURED_RESPONSE_NAME = "structured_response"


def _json_pointer(root: dict, ref: str) -> Any:
    """Resolve one RFC 6901 local pointer against the canonical schema."""
    if not ref.startswith("#/"):
        raise ValueError(f"function_calling requires a local schema ref: {ref!r}")
    current: Any = root
    for raw_part in ref[2:].split("/"):
        part = raw_part.replace("~1", "/").replace("~0", "~")
        if not isinstance(current, dict) or part not in current:
            raise ValueError(f"unresolved local schema ref: {ref!r}")
        current = current[part]
    return current


def _inline_local_refs(schema: dict) -> dict:
    """Return an equivalent non-recursive schema without local ref containers.

    SGLang's function-tool grammar rejects otherwise valid Pydantic schemas that
    retain ``$defs/$ref``.  Only the provider copy is normalized; the canonical
    schema remains the post-validation and evidence contract.
    """
    root = copy.deepcopy(schema)

    def visit(value: Any, resolving: tuple[str, ...] = ()) -> Any:
        if isinstance(value, list):
            return [visit(item, resolving) for item in value]
        if not isinstance(value, dict):
            return value
        if "$ref" in value:
            siblings = set(value) - {"$ref", "$defs", "definitions"}
            if siblings:
                raise ValueError(
                    "local schema ref with sibling keywords cannot be safely inlined: "
                    f"{sorted(siblings)!r}"
                )
            ref = value["$ref"]
            if not isinstance(ref, str):
                raise ValueError("schema $ref must be a string")
            if ref in resolving:
                chain = " -> ".join((*resolving, ref))
                raise ValueError(f"recursive local schema ref is unsupported: {chain}")
            return visit(copy.deepcopy(_json_pointer(root, ref)), (*resolving, ref))
        return {
            key: visit(item, resolving)
            for key, item in value.items()
            if key not in {"$defs", "definitions"}
        }

    result = visit(root)
    if not isinstance(result, dict):
        raise ValueError("structured output schema root must be an object")
    return result


def provider_structured_output_schema(schema: dict, *, method: str) -> dict:
    """Return the provider-compatible semantic schema without binding metadata."""
    if method not in STRUCTURED_OUTPUT_METHODS:
        raise ValueError(f"unsupported structured output method: {method!r}")
    provider_schema = copy.deepcopy(schema)
    if method == "function_calling":
        provider_schema = _inline_local_refs(provider_schema)
    return provider_schema


def provider_structured_output_binding_schema(
    schema: dict,
    *,
    method: str,
) -> dict:
    """Return the adapter copy with its one required stable protocol name.

    LangChain converts the root ``title`` into the OpenAI response/tool name and
    removes it from the nested semantic schema.  Keeping this on a separate copy
    preserves the title-free canonical contract while satisfying every supported
    structured-output binding method.
    """
    binding_schema = provider_structured_output_schema(schema, method=method)
    binding_schema["title"] = STRUCTURED_RESPONSE_NAME
    return binding_schema


def bind_structured_output(llm: Any, schema: dict, *, method: str) -> Any:
    """Bind one canonical schema without allowing adapter in-place mutation."""
    provider_schema = provider_structured_output_binding_schema(
        schema,
        method=method,
    )
    if method == "provider_default":
        return llm.with_structured_output(provider_schema, include_raw=True)
    if method == "function_calling":
        return llm.with_structured_output(
            provider_schema,
            method=method,
            include_raw=True,
            strict=True,
        )
    return llm.with_structured_output(
        provider_schema,
        method=method,
        include_raw=True,
    )
