from __future__ import annotations

import copy
import hashlib

import pytest

from pipeline.steps.step2_load_ast.evidence_ir import (
    CommonEvidenceContractError,
    validate_common_evidence,
)


KINDS = (
    "call", "callable", "import", "symbol", "literal", "assignment",
    "parameter", "macro", "embedded_language", "conditional_region",
)


def _range(source: str, value: str, *, last: bool = False) -> dict[str, int]:
    offset = source.rindex(value) if last else source.index(value)
    return {"charOffset": offset, "charLength": len(value)}


def _fact_id(
    source_id: str,
    decoded_hash: str,
    kind: str,
    range_value: dict[str, int],
) -> str:
    value = "\0".join((
        "robo-evidence-v1", source_id, decoded_hash, kind,
        f"{range_value['charOffset']}:{range_value['charLength']}", "0",
    ))
    return hashlib.sha256(value.encode()).hexdigest()


def _configured_preprocessing(source_id: str) -> dict:
    build_id = hashlib.sha256(f"build\0{source_id}".encode()).hexdigest()
    trace_id = hashlib.sha256(f"trace\0{source_id}".encode()).hexdigest()
    return {
        "version": "1.0.0", "status": "unresolved", "trust": "unresolved",
        "build": {
            "status": "unresolved", "population": 1, "emitted": 0,
            "explicitlyUnresolved": 1, "commandOccurrenceIds": [],
            "emittedCommandOccurrenceIds": [],
            "unresolvedCommandOccurrenceIds": [],
            "unresolvedEvidenceIds": [build_id],
            "reasons": ["insufficient_compilation_database"],
        },
        "trace": {
            "status": "unresolved", "population": 1, "emitted": 0,
            "explicitlyUnresolved": 1, "evidenceIds": [trace_id],
            "emittedEvidenceIds": [], "unresolvedEvidenceIds": [trace_id],
            "reasons": ["insufficient_preprocessing_build_context"],
        },
    }


def _artifact() -> tuple[str, dict]:
    source = (
        "static int target(int value);\n"
        "int caller(void) { return target(1); }\n"
    )
    source_id = "slice.c"
    digest = hashlib.sha256(source.encode()).hexdigest()
    unit_range = {"charOffset": 0, "charLength": len(source)}
    declaration_range = _range(source, "static int target(int value)")
    declarator_range = _range(source, "target(int value)")
    name_range = _range(source, "target")
    call_range = _range(source, "target(1)", last=True)
    call_name_range = _range(source, "target", last=True)
    facts = [
        {
            "factId": _fact_id(source_id, digest, "callable", declaration_range),
            "kind": "callable",
            "range": declaration_range,
            "grammarRuleRef": 0,
            "presenceRef": 0,
            "payload": {
                "role": "declaration",
                "nameRange": name_range,
                "astNodeRange": None,
                "scopePath": [{"kind": "translation_unit", "range": unit_range}],
                "scopeRange": unit_range,
                "declarationPoint": declarator_range["charOffset"]
                + declarator_range["charLength"],
                "syntax": {
                    "schema": "c-callable-syntax/v1",
                    "declarationSpecifiers": [
                        {
                            "grammarRule": "storageClassSpecifier",
                            "range": _range(source, "static"),
                            "directTokens": [{
                                "tokenKind": "Static",
                                "range": _range(source, "static"),
                            }],
                            "children": [],
                        },
                        {
                            "grammarRule": "typeSpecifier",
                            "range": _range(source, "int"),
                            "directTokens": [{
                                "tokenKind": "Int",
                                "range": _range(source, "int"),
                            }],
                            "children": [],
                        },
                    ],
                    "declarator": {
                        "grammarRule": "declarator",
                        "range": declarator_range,
                        "directTokens": [{
                            "tokenKind": "Identifier", "range": name_range,
                        }],
                        "children": [],
                    },
                    "attributes": [],
                },
            },
        },
        {
            "factId": _fact_id(source_id, digest, "call", call_range),
            "kind": "call",
            "range": call_range,
            "grammarRuleRef": 1,
            "presenceRef": 0,
            "payload": {
                "calleeRange": call_name_range,
                "receiverRange": None,
                "calleeKind": "named",
                "terminalName": "target",
                "argumentRanges": [_range(source, "1")],
                "scopePath": [{"kind": "translation_unit", "range": unit_range}],
            },
        },
    ]
    completeness = []
    for kind in KINDS:
        emitted = 1 if kind in {"call", "callable"} else 0
        completeness.append({
            "kind": kind,
            "status": "complete" if kind in {
                "call", "callable", "import", "symbol", "macro",
                "conditional_region",
            } else "unsupported",
            "population": emitted,
            "emitted": emitted,
            "explicitlyUnresolved": 0,
        })
    return source, {"evidence": {
        "version": "2.0.0",
        "language": "c",
        "frontendSchema": "antlr-c/v1",
        "sourceId": source_id,
        "rawSourceSha256": digest,
        "decodedTextSha256": digest,
        "decodedText": source,
        "sourceEncoding": "UTF-8",
        "decodeStatus": "exact",
        "positionEncoding": "unicode-code-point",
        "rangeConvention": "half-open",
        "rangeEncoding": "char-offset-length",
        "parseStatus": "exact",
        "configuredPreprocessing": _configured_preprocessing(source_id),
        "facts": facts,
        "grammarRules": ["declaration", "postfixExpression"],
        "presences": [{
            "status": "active", "condition": None,
            "configurationId": None, "provenance": "unconditional",
        }],
        "completeness": completeness,
    }}


def _validate(raw: dict, source: str):
    return validate_common_evidence(
        raw, source_bytes=source.encode(), expected_source_id="slice.c",
    )


def test_ir2_decodes_structural_evidence_without_parser_semantics() -> None:
    source, raw = _artifact()
    evidence = _validate(raw, source)

    assert (evidence.language, evidence.frontend_schema) == ("c", "antlr-c/v1")
    assert len(evidence.call_facts) == len(evidence.callable_facts) == 1
    assert evidence.call_facts[0].scope_path[0].kind == "translation_unit"
    callable_fact = evidence.callable_facts[0]
    assert callable_fact.terminal_name == "target"
    assert callable_fact.syntax.declaration_specifiers[0].direct_tokens[0].text == "static"
    record = callable_fact.as_record(code_node_id=None)
    assert "binding" not in record and "binding_key" not in record


def test_ir2_rejects_parser_owned_final_semantics() -> None:
    source, raw = _artifact()
    raw["evidence"]["facts"][1]["payload"]["binding"] = {}

    with pytest.raises(CommonEvidenceContractError, match="fields"):
        _validate(raw, source)


def test_ir2_rejects_duplicate_direct_terminal_coordinates() -> None:
    source, raw = _artifact()
    syntax = raw["evidence"]["facts"][0]["payload"]["syntax"]
    syntax["declarator"]["directTokens"].append(copy.deepcopy(
        syntax["declarator"]["directTokens"][0]
    ))

    with pytest.raises(CommonEvidenceContractError, match="direct terminal"):
        _validate(raw, source)


def test_ir13_parser_semantic_wire_is_not_a_supported_fallback() -> None:
    source, raw = _artifact()
    raw["evidence"]["version"] = "1.3.0"

    with pytest.raises(CommonEvidenceContractError, match="supported versions"):
        _validate(raw, source)
