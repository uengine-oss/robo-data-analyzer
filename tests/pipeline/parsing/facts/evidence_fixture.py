from __future__ import annotations

import hashlib
from typing import Any, Iterable


KINDS = (
    "call", "import", "symbol", "literal", "assignment", "parameter",
    "macro", "embedded_language", "conditional_region",
)


def _range(source: str, expression: str) -> dict[str, int]:
    offset = source.index(expression)
    return {"charOffset": offset, "charLength": len(expression)}


def _fact_id(source_id: str, decoded_hash: str, kind: str,
             fact_range: dict[str, int], ordinal: int = 0) -> str:
    value = "\0".join((
        "robo-evidence-v1", source_id, decoded_hash, kind,
        f"{fact_range['charOffset']}:{fact_range['charLength']}", str(ordinal),
    ))
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def add_common_evidence(
    raw: dict[str, Any],
    source: str,
    *,
    calls: Iterable[dict[str, Any]] = (),
    call_supported: bool = True,
) -> dict[str, Any]:
    """Attach a minimal valid v1 envelope to a hand-authored Parser fixture."""
    _add_identifier_contract(raw)
    source_id = str(raw["filePath"]).replace("\\", "/")
    decoded_hash = hashlib.sha256(source.encode("utf-8")).hexdigest()
    presences = [{
        "status": "active", "condition": None,
        "configurationId": None, "provenance": "unconditional",
    }]
    facts = []
    ordinals: dict[tuple[int, int], int] = {}
    for call in calls:
        call_range = _range(source, call["expression"])
        ordinal_key = (call_range["charOffset"], call_range["charLength"])
        ordinal = ordinals.get(ordinal_key, 0)
        ordinals[ordinal_key] = ordinal + 1
        facts.append({
            "factId": _fact_id(source_id, decoded_hash, "call", call_range, ordinal),
            "kind": "call",
            "range": call_range,
            "grammarRuleRef": 0,
            "presenceRef": 0,
            "payload": {
                "calleeRange": _range(source, call["callee"]),
                "calleeKind": call.get("callee_kind", "named"),
                "terminalName": call.get("terminal_name"),
                "argumentRanges": [
                    _range(source, argument) for argument in call.get("arguments", ())
                ],
            },
        })
    completeness = []
    for kind in KINDS:
        emitted = len(facts) if kind == "call" else 0
        status = (
            "complete" if kind == "conditional_region"
            else "complete" if kind == "call" and call_supported
            else "unsupported"
        )
        completeness.append({
            "kind": kind, "status": status,
            "population": emitted, "emitted": emitted,
            "explicitlyUnresolved": 0,
        })
    evidence = {
        "version": "1.0.0",
        "sourceId": source_id,
        "rawSourceSha256": decoded_hash,
        "decodedTextSha256": decoded_hash,
        "decodedText": source,
        "sourceEncoding": "UTF-8",
        "decodeStatus": "exact",
        "positionEncoding": "unicode-code-point",
        "rangeConvention": "half-open",
        "rangeEncoding": "char-offset-length",
        "parseStatus": "exact",
        "facts": facts,
        "grammarRules": ["postfixExpression"] if facts else [],
        "presences": presences if facts else [],
        "completeness": completeness,
    }
    if source_id.lower().endswith((".c", ".h")):
        evidence["configuredPreprocessing"] = _unresolved_c_preprocessing(source_id)
    raw["evidence"] = evidence
    return raw


def _add_identifier_contract(node: dict[str, Any]) -> None:
    for child in node.get("children") or ():
        child.setdefault("identifierReferenceVersion", 1)
        _add_identifier_contract(child)


def _unresolved_c_preprocessing(source_id: str) -> dict[str, Any]:
    build_id = hashlib.sha256(
        f"fixture-build\0{source_id}".encode("utf-8")
    ).hexdigest()
    trace_id = hashlib.sha256(
        f"fixture-trace\0{source_id}".encode("utf-8")
    ).hexdigest()
    return {
        "version": "1.0.0",
        "status": "unresolved",
        "trust": "unresolved",
        "build": {
            "status": "unresolved",
            "population": 1,
            "emitted": 0,
            "explicitlyUnresolved": 1,
            "commandOccurrenceIds": [],
            "emittedCommandOccurrenceIds": [],
            "unresolvedCommandOccurrenceIds": [],
            "unresolvedEvidenceIds": [build_id],
            "reasons": ["insufficient_compilation_database"],
        },
        "trace": {
            "status": "unresolved",
            "population": 1,
            "emitted": 0,
            "explicitlyUnresolved": 1,
            "evidenceIds": [trace_id],
            "emittedEvidenceIds": [],
            "unresolvedEvidenceIds": [trace_id],
            "reasons": ["insufficient_preprocessing_build_context"],
        },
    }
