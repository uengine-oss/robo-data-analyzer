from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from pipeline.stages.ast.ast_dispatch import read_antlr_json
from tests.pipeline.parser_reference_fixtures import with_identifier_contract
from pipeline.parsing.facts.evidence_ir import (
    CommonEvidenceContractError,
    validate_common_evidence,
)
from shared.files.source_cache import SourceFileCache


KINDS = (
    "call", "import", "symbol", "literal", "assignment", "parameter",
    "macro", "embedded_language", "conditional_region",
)


def _fact_id(source_id: str, decoded_hash: str, kind: str,
             offset: int, length: int, ordinal: int = 0) -> str:
    value = "\0".join((
        "robo-evidence-v1", source_id, decoded_hash, kind,
        f"{offset}:{length}", str(ordinal),
    ))
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _range(source: str, expression: str) -> dict[str, int]:
    offset = source.index(expression)
    return {"charOffset": offset, "charLength": len(expression)}


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


def _artifact(source: str, *, second_presence: str = "active") -> dict:
    source_id = "sample.c"
    decoded_hash = hashlib.sha256(source.encode("utf-8")).hexdigest()
    active = {
        "status": "active", "condition": None,
        "configurationId": None, "provenance": "unconditional",
    }
    second = {
        "status": second_presence,
        "condition": "#if 0" if second_presence == "inactive" else None,
        "configurationId": None,
        "provenance": "constant_evaluated" if second_presence == "inactive"
        else "unconditional",
    }
    calls = []
    for expression, callee, kind, terminal, argument, presence_ref in (
        ("service->handler(1)", "service->handler", "named", "handler", "1", 0),
        ("(*callback)(2)", "(*callback)", "expression", None, "2", 1),
    ):
        call_range = _range(source, expression)
        calls.append({
            "factId": _fact_id(
                source_id, decoded_hash, "call",
                call_range["charOffset"], call_range["charLength"],
            ),
            "kind": "call",
            "range": call_range,
            "grammarRuleRef": 0,
            "presenceRef": presence_ref,
            "payload": {
                "calleeRange": _range(source, callee),
                "calleeKind": kind,
                "terminalName": terminal,
                "argumentRanges": [_range(source, argument)],
            },
        })
    completeness = []
    for kind in KINDS:
        emitted = len(calls) if kind == "call" else 0
        completeness.append({
            "kind": kind,
            "status": "complete" if kind in {"call", "conditional_region"}
            else "unsupported",
            "population": emitted,
            "emitted": emitted,
            "explicitlyUnresolved": 0,
        })
    return {
        "type": "FILE", "fileName": "sample.c", "filePath": source_id,
        "children": [{
            "type": "FUNCTION", "name": "run", "signature": "void run(void)",
            "startLine": 1, "endLine": 1,
            "children": [
                {"type": "FUNCTION_CALL", "name": "handler", "startLine": 1,
                 "endLine": 1, "children": []},
                {"type": "FUNCTION_CALL", "name": "", "startLine": 1,
                 "endLine": 1, "children": []},
            ],
        }],
        "evidence": {
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
            "configuredPreprocessing": _configured_preprocessing(source_id),
            "facts": calls,
            "grammarRules": ["postfixExpression"],
            "presences": [active, second],
            "completeness": completeness,
        },
    }


def _read(raw: dict, source: str):
    temporary = TemporaryDirectory()
    root = Path(temporary.name)
    (root / "sample.c").write_text(source, encoding="utf-8", newline="")
    artifact = root / "sample.json"
    artifact.write_text(json.dumps(with_identifier_contract(raw)), encoding="utf-8")
    return temporary, read_antlr_json(artifact, root)


def _macro_artifact(source: str) -> dict:
    source_id = "sample.c"
    decoded_hash = hashlib.sha256(source.encode("utf-8")).hexdigest()
    definition = source.rstrip("\n")
    fact_range = _range(source, definition)
    fact = {
        "factId": _fact_id(
            source_id, decoded_hash, "macro",
            fact_range["charOffset"], fact_range["charLength"],
        ),
        "kind": "macro",
        "range": fact_range,
        "grammarRuleRef": 0,
        "presenceRef": 0,
        "payload": {
            "nameRange": _range(source, "TRACE"),
            "macroKind": "function",
            "terminalName": "TRACE",
            "parameterRanges": [_range(source, "value")],
            "variadic": True,
            "replacementRange": _range(
                source, "log_trace(value, __VA_ARGS__)"
            ),
        },
    }
    completeness = []
    for kind in KINDS:
        emitted = 1 if kind == "macro" else 0
        completeness.append({
            "kind": kind,
            "status": "complete" if kind in {"macro", "conditional_region"}
            else "unsupported",
            "population": emitted,
            "emitted": emitted,
            "explicitlyUnresolved": 0,
        })
    return {"evidence": {
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
        "configuredPreprocessing": _configured_preprocessing(source_id),
        "facts": [fact],
        "grammarRules": ["functionDefine"],
        "presences": [{
            "status": "active", "condition": None,
            "configurationId": None, "provenance": "unconditional",
        }],
        "completeness": completeness,
    }}


def _import_artifact() -> tuple[str, dict]:
    source_id = "sample.c"
    source = (
        "#include <local.h>\n"
        "#include \"quoted.h\"\n"
        "#inc\\\nlude <spliced.h>\n"
        "#include <한글.h>\n"
    )
    decoded_hash = hashlib.sha256(source.encode("utf-8")).hexdigest()
    active = {
        "status": "active", "condition": None,
        "configurationId": None, "provenance": "unconditional",
    }
    inputs = (
        ("#include <local.h>", "<local.h>", "angle", 1, 1),
        ('#include "quoted.h"', '"quoted.h"', "quoted", 2, 2),
        ("#inc\\\nlude <spliced.h>", "<spliced.h>", "angle", 3, 4),
        ("#include <한글.h>", "<한글.h>", "angle", 5, 5),
    )
    facts = []
    children = []
    for directive, target, target_kind, start_line, end_line in inputs:
        fact_range = _range(source, directive)
        facts.append({
            "factId": _fact_id(
                source_id, decoded_hash, "import",
                fact_range["charOffset"], fact_range["charLength"],
            ),
            "kind": "import",
            "range": fact_range,
            "grammarRuleRef": 0,
            "presenceRef": 0,
            "payload": {
                "directiveKind": "include",
                "targetKind": target_kind,
                "targetRange": _range(source, target),
            },
        })
        children.append({
            "type": "INCLUDE", "name": target,
            "startLine": start_line, "endLine": end_line, "children": [],
        })
    completeness = []
    for kind in KINDS:
        emitted = len(facts) if kind == "import" else 0
        completeness.append({
            "kind": kind,
            "status": "complete" if kind in {
                "call", "import", "macro", "conditional_region",
            } else "unsupported",
            "population": emitted,
            "emitted": emitted,
            "explicitlyUnresolved": 0,
        })
    return source, {
        "type": "FILE", "fileName": "sample.c", "filePath": source_id,
        "children": children,
        "evidence": {
            "version": "1.0.0", "sourceId": source_id,
            "rawSourceSha256": decoded_hash,
            "decodedTextSha256": decoded_hash, "decodedText": source,
            "sourceEncoding": "UTF-8", "decodeStatus": "exact",
            "positionEncoding": "unicode-code-point",
            "rangeConvention": "half-open",
            "rangeEncoding": "char-offset-length", "parseStatus": "exact",
            "configuredPreprocessing": _configured_preprocessing(source_id),
            "facts": facts, "grammarRules": ["includeLine"],
            "presences": [active], "completeness": completeness,
        },
    }


def _structured_import_artifact(
    *, source_id: str, source: str, directive_kind: str,
    entries: list[dict], legacy_name: str,
) -> dict:
    decoded_hash = hashlib.sha256(source.encode("utf-8")).hexdigest()
    statement = source.rstrip("\n")
    fact_range = _range(source, statement)
    fact = {
        "factId": _fact_id(
            source_id, decoded_hash, "import",
            fact_range["charOffset"], fact_range["charLength"],
        ),
        "kind": "import",
        "range": fact_range,
        "grammarRuleRef": 0,
        "presenceRef": 0,
        "payload": {
            "directiveKind": directive_kind,
            "entries": entries,
        },
    }
    completeness = []
    for kind in KINDS:
        emitted = 1 if kind == "import" else 0
        completeness.append({
            "kind": kind,
            "status": "complete",
            "population": emitted,
            "emitted": emitted,
            "explicitlyUnresolved": 0,
        })
    evidence = {
        "version": "1.1.0", "sourceId": source_id,
        "rawSourceSha256": decoded_hash,
        "decodedTextSha256": decoded_hash, "decodedText": source,
        "sourceEncoding": "UTF-8", "decodeStatus": "exact",
        "positionEncoding": "unicode-code-point",
        "rangeConvention": "half-open",
        "rangeEncoding": "char-offset-length", "parseStatus": "exact",
        "facts": [fact], "grammarRules": ["importStatement"],
        "presences": [{
            "status": "active", "condition": None,
            "configurationId": None, "provenance": "unconditional",
        }],
        "completeness": completeness,
    }
    if source_id.endswith((".c", ".h")):
        evidence["configuredPreprocessing"] = _configured_preprocessing(source_id)
    return {
        "type": "FILE", "fileName": Path(source_id).name,
        "filePath": source_id,
        "children": [{
            "type": "INCLUDE" if directive_kind == "include" else "IMPORT",
            "name": legacy_name, "startLine": 1, "endLine": 1,
            "children": [],
        }],
        "evidence": evidence,
    }


def _symbol_artifact(*, ambiguous: bool = False) -> tuple[str, dict]:
    source_id = "sample.c"
    source = (
        "typedef int KnownType;\n"
        "KnownType value;\n"
        "void run(void) { MaybeType *local; }\n"
    )
    decoded_hash = hashlib.sha256(source.encode("utf-8")).hexdigest()
    known_definition_range = _range(source, "KnownType")
    known_lookup_offset = source.index(
        "KnownType",
        known_definition_range["charOffset"] + known_definition_range["charLength"],
    )
    known_lookup_range = {
        "charOffset": known_lookup_offset,
        "charLength": len("KnownType"),
    }
    missing_range = _range(source, "MaybeType")
    definition_id = _fact_id(
        source_id, decoded_hash, "symbol",
        known_definition_range["charOffset"], known_definition_range["charLength"],
    )
    facts = [
        {
            "factId": definition_id,
            "kind": "symbol",
            "range": known_definition_range,
            "grammarRuleRef": 0,
            "presenceRef": 0,
            "payload": {
                "role": "definition",
                "symbolKind": "typedef_name",
                "scopeKind": "file",
                "scopeRange": {"charOffset": 0, "charLength": len(source)},
                "visibilityStartOffset": source.index(";") + 1,
            },
        },
        {
            "factId": _fact_id(
                source_id, decoded_hash, "symbol",
                known_lookup_range["charOffset"], known_lookup_range["charLength"],
            ),
            "kind": "symbol",
            "range": known_lookup_range,
            "grammarRuleRef": 1,
            "presenceRef": 0,
            "payload": {
                "role": "lookup",
                "lookupKind": "type_name",
                "parserDecision": "type_name",
                "resolutionStatus": "resolved",
                "provenance": "source_declaration",
                "definitionFactId": definition_id,
                "configuredEvidenceId": None,
                "predicateContexts": ["typedef_name"],
            },
        },
        {
            "factId": _fact_id(
                source_id, decoded_hash, "symbol",
                missing_range["charOffset"], missing_range["charLength"],
            ),
            "kind": "symbol",
            "range": missing_range,
            "grammarRuleRef": 1,
            "presenceRef": 0,
            "payload": {
                "role": "lookup",
                "lookupKind": "type_name",
                "parserDecision": (
                    "ordinary_identifier" if ambiguous else "type_name"
                ),
                "resolutionStatus": "unresolved",
                "provenance": "unresolved_environment",
                "definitionFactId": None,
                "configuredEvidenceId": None,
                "predicateContexts": [
                    "ambiguous_block_item" if ambiguous
                    else "declaration_only_parse"
                ],
            },
        },
    ]
    unresolved_id = facts[-1]["factId"]
    completeness = []
    for kind in KINDS:
        emitted = len(facts) if kind == "symbol" else 0
        row = {
            "kind": kind,
            "status": (
                "partial" if kind == "symbol"
                else "complete" if kind in {
                    "call", "import", "macro", "conditional_region",
                } else "unsupported"
            ),
            "population": emitted,
            "emitted": emitted,
            "explicitlyUnresolved": 0,
        }
        if kind == "symbol":
            row.update(
                reason="insufficient_type_name_environment",
                reasons=["insufficient_type_name_environment"],
                unresolvedFactIds=[unresolved_id],
            )
        completeness.append(row)
    return source, {
        "type": "FILE", "fileName": "sample.c", "filePath": source_id,
        "children": [{
            "type": "FUNCTION", "name": "run", "signature": "void run(void)",
            "startLine": 3, "endLine": 3, "children": [],
        }],
        "evidence": {
            "version": "1.2.0", "sourceId": source_id,
            "rawSourceSha256": decoded_hash,
            "decodedTextSha256": decoded_hash, "decodedText": source,
            "sourceEncoding": "UTF-8", "decodeStatus": "exact",
            "positionEncoding": "unicode-code-point",
            "rangeConvention": "half-open",
            "rangeEncoding": "char-offset-length", "parseStatus": "partial",
            "configuredPreprocessing": _configured_preprocessing(source_id),
            "facts": facts,
            "grammarRules": ["declarator", "typedefNameDecision"],
            "presences": [{
                "status": "active", "condition": None,
                "configurationId": None, "provenance": "unconditional",
            }],
            "completeness": completeness,
        },
    }


def test_v12_symbol_facts_are_strict_diagnostics_not_graph_entities() -> None:
    source, raw = _symbol_artifact()
    validated = validate_common_evidence(
        raw, source_bytes=source.encode("utf-8"), expected_source_id="sample.c",
    )

    assert validated.version == "1.2.0"
    assert len(validated.symbol_facts) == 3
    assert [fact.role for fact in validated.symbol_facts] == [
        "definition", "lookup", "lookup",
    ]
    assert validated.syntax_ambiguities == ()
    temporary, result = _read(raw, source)
    try:
        assert not hasattr(result, "symbol_facts")
        assert result.evidence_completeness[-7]["kind"] == "symbol"
    finally:
        temporary.cleanup()


@pytest.mark.parametrize(
    ("mutate", "message"),
    (
        (
            lambda raw: raw["evidence"]["facts"][1].update(
                payload={
                    **raw["evidence"]["facts"][1]["payload"],
                    "definitionFactId": "f" * 64,
                },
            ),
            "definitionFactId",
        ),
        (
            lambda raw: next(
                row for row in raw["evidence"]["completeness"]
                if row["kind"] == "symbol"
            ).update(unresolvedFactIds=[]),
            "unresolvedFactIds",
        ),
        (
            lambda raw: raw["evidence"]["facts"][0]["payload"].update(
                visibilityStartOffset=0,
            ),
            "visibilityStartOffset",
        ),
        (
            lambda raw: raw["evidence"]["facts"][1]["payload"].update(
                predicateContexts=[],
            ),
            "predicateContexts",
        ),
        (
            lambda raw: raw["evidence"]["facts"][0]["payload"].update(
                symbolKind="ordinary_identifier",
            ),
            "parserDecision",
        ),
        (
            lambda raw: next(
                row for row in raw["evidence"]["completeness"]
                if row["kind"] == "symbol"
            ).update(status="complete"),
            "completeness\\[symbol\\]",
        ),
        (
            lambda raw: raw["evidence"]["facts"][1]["payload"].update(
                provenance="configured_preprocessing",
                definitionFactId=None,
                configuredEvidenceId="a" * 64,
            ),
            "configuredEvidenceId",
        ),
        (
            lambda raw: raw["evidence"]["facts"][1]["payload"].update(
                provenance="grammar_context",
                definitionFactId=None,
            ),
            "parserDecision",
        ),
    ),
)
def test_v12_malformed_symbol_contract_fails_closed(mutate, message) -> None:
    source, raw = _symbol_artifact()
    mutate(raw)
    with pytest.raises(CommonEvidenceContractError, match=message):
        validate_common_evidence(
            raw, source_bytes=source.encode("utf-8"),
            expected_source_id="sample.c",
        )


def test_v12_ambiguous_type_name_decision_fails_before_ast_projection() -> None:
    source, raw = _symbol_artifact(ambiguous=True)
    with TemporaryDirectory() as temp:
        root = Path(temp)
        (root / "sample.c").write_text(source, encoding="utf-8", newline="")
        artifact = root / "sample.json"
        artifact.write_text(
            json.dumps(with_identifier_contract(raw)), encoding="utf-8",
        )
        with pytest.raises(CommonEvidenceContractError, match="syntax ambiguity"):
            read_antlr_json(artifact, root)


def _structured_import_cases() -> list[tuple[str, str, dict, str]]:
    c_source = '#include "local/config.h"\n'
    c_entry = {
        "importKind": "source_file", "targetKind": "quoted",
        "targetRange": _range(c_source, '"local/config.h"'),
        "pathComponentRanges": [_range(c_source, "local/config.h")],
        "memberRange": None, "aliasRange": None, "relativeLevel": 0,
        "wildcard": False, "locality": "local",
    }
    java_source = "import static pkg.Constants.VALUE;\n"
    java_entry = {
        "importKind": "static_member", "targetKind": "qualified",
        "targetRange": _range(java_source, "pkg.Constants.VALUE"),
        "pathComponentRanges": [
            _range(java_source, "pkg"), _range(java_source, "Constants"),
        ],
        "memberRange": _range(java_source, "VALUE"), "aliasRange": None,
        "relativeLevel": 0, "wildcard": False, "locality": "unspecified",
    }
    python_source = "from ..base.tools import first as one\n"
    python_entry = {
        "importKind": "module_member", "targetKind": "qualified",
        "targetRange": _range(python_source, "first as one"),
        "pathComponentRanges": [
            _range(python_source, "base"), _range(python_source, "tools"),
        ],
        "memberRange": _range(python_source, "first"),
        "aliasRange": _range(python_source, "one"), "relativeLevel": 2,
        "wildcard": False, "locality": "unspecified",
    }
    return [
        ("sample.c", c_source, c_entry, '"local/config.h"'),
        ("Sample.java", java_source, java_entry, "static pkg.Constants.VALUE"),
        ("sample.py", python_source, python_entry,
         "from ..base.tools import first as one"),
    ]


@pytest.mark.parametrize(
    ("source_id", "source", "entry", "legacy_name"),
    _structured_import_cases(),
)
def test_v11_import_bindings_preserve_grammar_owned_structure(
    source_id: str, source: str, entry: dict, legacy_name: str,
) -> None:
    raw = _structured_import_artifact(
        source_id=source_id, source=source,
        directive_kind="include" if source_id.endswith(".c") else "import",
        entries=[entry], legacy_name=legacy_name,
    )
    validated = validate_common_evidence(
        raw, source_bytes=source.encode("utf-8"),
        expected_source_id=source_id,
    )

    assert validated.version == "1.1.0"
    assert len(validated.import_facts) == 1
    fact = validated.import_facts[0]
    assert len(fact.entries) == 1
    binding = fact.entries[0]
    assert binding.import_kind == entry["importKind"]
    assert binding.target_kind == entry["targetKind"]
    assert source[binding.target_range.char_offset:binding.target_range.end] == source[
        entry["targetRange"]["charOffset"]:
        entry["targetRange"]["charOffset"] + entry["targetRange"]["charLength"]
    ]
    assert [
        source[item.char_offset:item.end] for item in binding.path_component_ranges
    ] == [
        source[item["charOffset"]:item["charOffset"] + item["charLength"]]
        for item in entry["pathComponentRanges"]
    ]
    assert binding.relative_level == entry["relativeLevel"]
    assert binding.wildcard is entry["wildcard"]
    assert binding.locality == entry["locality"]


@pytest.mark.parametrize(
    ("source_id", "source", "entry", "legacy_name"),
    _structured_import_cases(),
)
def test_v11_framework_binding_uses_entry_ranges_without_retaining_tree_preview(
    source_id: str, source: str, entry: dict, legacy_name: str,
) -> None:
    raw = _structured_import_artifact(
        source_id=source_id, source=source,
        directive_kind="include" if source_id.endswith(".c") else "import",
        entries=[entry], legacy_name=f"preview-only:{legacy_name}",
    )
    with TemporaryDirectory() as temp:
        root = Path(temp)
        source_path = root / source_id
        source_path.parent.mkdir(parents=True, exist_ok=True)
        source_path.write_text(source, encoding="utf-8", newline="")
        artifact = root / f"{source_path.name}.json"
        artifact.write_text(
            json.dumps(with_identifier_contract(raw)), encoding="utf-8",
        )
        result = read_antlr_json(artifact, root)

    assert len(result.import_refs) == 1
    ref = result.import_refs[0]
    assert ref["evidence_version"] == "1.1.0"
    assert "legacy_name" not in ref and "name" not in ref
    assert "file" not in ref and "presence" not in ref
    assert ref["entries"][0]["import_kind"] == entry["importKind"]
    assert ref["entries"][0]["target_kind"] == entry["targetKind"]
    assert ref["entries"][0]["entry_ordinal"] == 0


@pytest.mark.parametrize(
    ("mutate", "message"),
    (
        (lambda payload, entry: payload.update(extra="not canonical"), "import.payload"),
        (lambda payload, entry: payload.update(entries=[]), "import.payload.entries"),
        (lambda payload, entry: entry.update(importKind="package"), "importKind"),
        (lambda payload, entry: entry.update(targetKind="text"), "targetKind"),
        (lambda payload, entry: entry.update(pathComponentRanges=[]), "source_file"),
        (lambda payload, entry: entry.update(wildcard=True), "source_file"),
        (lambda payload, entry: entry.update(extra="not canonical"), "unexpected"),
    ),
)
def test_v11_malformed_import_binding_fails_closed(mutate, message) -> None:
    source_id, source, entry, legacy_name = _structured_import_cases()[0]
    raw = _structured_import_artifact(
        source_id=source_id, source=source, directive_kind="include",
        entries=[copy.deepcopy(entry)], legacy_name=legacy_name,
    )
    payload = raw["evidence"]["facts"][0]["payload"]
    mutate(payload, payload["entries"][0])
    with pytest.raises(CommonEvidenceContractError, match=message):
        validate_common_evidence(
            raw, source_bytes=source.encode("utf-8"),
            expected_source_id=source_id,
        )


def test_import_facts_are_strict_exact_source_occurrences() -> None:
    source, raw = _import_artifact()
    validated = validate_common_evidence(
        raw, source_bytes=source.encode("utf-8"), expected_source_id="sample.c",
    )

    assert len(validated.import_facts) == 4
    assert [fact.fact_id for fact in validated.import_facts] == [
        fact["factId"] for fact in raw["evidence"]["facts"]
    ]
    assert [
        source[fact.entries[0].target_range.char_offset:fact.entries[0].target_range.end]
        for fact in validated.import_facts
    ] == ["<local.h>", '"quoted.h"', "<spliced.h>", "<한글.h>"]
    assert [fact.entries[0].target_kind for fact in validated.import_facts] == [
        "angle", "quoted", "angle", "angle",
    ]


def test_framework_imports_bind_canonical_ids_ranges_and_physical_lines() -> None:
    source, raw = _import_artifact()
    temporary, result = _read(raw, source)
    try:
        assert [ref["import_id"] for ref in result.import_refs] == [
            fact["factId"] for fact in raw["evidence"]["facts"]
        ]
        assert [ref["entries"][0]["target"] for ref in result.import_refs] == [
            "<local.h>", '"quoted.h"', "<spliced.h>", "<한글.h>",
        ]
        assert [(ref["line"], ref["end_line"]) for ref in result.import_refs] == [
            (1, 1), (2, 2), (3, 4), (5, 5),
        ]
        assert len({ref["import_id"] for ref in result.import_refs}) == 4
        assert all(ref["grammar_rule"] == "includeLine" for ref in result.import_refs)
        assert all(ref["presence_status"] == "active" for ref in result.import_refs)
    finally:
        temporary.cleanup()


@pytest.mark.parametrize("status", ("unsupported", "not_applicable"))
def test_parser_unsupported_import_projection_stays_explicitly_degraded(
    status: str,
) -> None:
    source, raw = _import_artifact()
    raw["evidence"]["facts"] = []
    row = next(
        item for item in raw["evidence"]["completeness"]
        if item["kind"] == "import"
    )
    row.update(status=status, population=0, emitted=0, explicitlyUnresolved=0)
    temporary, result = _read(raw, source)
    try:
        assert len(result.import_refs) == 4
        assert all(ref["import_id"] == "" for ref in result.import_refs)
        assert all(ref["evidence_status"] == status for ref in result.import_refs)
        assert all(
            ref["evidence_reason"] == "parser_import_evidence_not_available"
            for ref in result.import_refs
        )
    finally:
        temporary.cleanup()


@pytest.mark.parametrize(
    ("mutate", "message"),
    (
        (
            lambda fact: fact["payload"].update(directiveKind="require"),
            "import.payload.directiveKind",
        ),
        (
            lambda fact: fact["payload"].update(targetKind="path"),
            "import.payload.targetKind",
        ),
        (
            lambda fact: fact["payload"].update(
                targetRange={"charOffset": 0, "charLength": 0},
            ),
            "import.payload.targetRange",
        ),
        (
            lambda fact: fact["payload"].update(extra="not canonical"),
            "import.payload",
        ),
    ),
)
def test_invalid_import_payload_fails_closed(mutate, message) -> None:
    source, raw = _import_artifact()
    mutate(raw["evidence"]["facts"][0])
    with pytest.raises(CommonEvidenceContractError, match=message):
        validate_common_evidence(
            raw, source_bytes=source.encode("utf-8"), expected_source_id="sample.c",
        )


@pytest.mark.parametrize(
    ("mutate", "message"),
    (
        (lambda raw: raw["children"].pop(), "import population"),
        (
            lambda raw: raw["children"][0].update(name="<different.h>"),
            "import traversal",
        ),
        (
            lambda raw: raw["children"][2].update(endLine=3),
            "import traversal",
        ),
    ),
)
def test_legacy_import_projection_must_match_canonical_facts_exactly_once(
    mutate, message,
) -> None:
    source, raw = _import_artifact()
    mutate(raw)
    with TemporaryDirectory() as temp:
        root = Path(temp)
        (root / "sample.c").write_text(source, encoding="utf-8", newline="")
        artifact = root / "sample.json"
        artifact.write_text(json.dumps(raw), encoding="utf-8")
        with pytest.raises(CommonEvidenceContractError, match=message):
            read_antlr_json(artifact, root)


def test_framework_calls_use_canonical_fact_ids_ranges_and_routine_owner() -> None:
    source = "void run(void) { service->handler(1); (*callback)(2); }\n"
    temporary, result = _read(_artifact(source), source)
    try:
        assert [site["call_id"] for site in result.call_sites] == [
            fact["factId"] for fact in _artifact(source)["evidence"]["facts"]
        ]
        assert len({site["call_id"] for site in result.call_sites}) == 2
        assert {site["caller_id"] for site in result.call_sites} == {
            "code:sample.c:run"
        }
        assert [site["callee_kind"] for site in result.call_sites] == [
            "named", "expression"
        ]
        assert [site["call_text"] for site in result.call_sites] == ["handler", ""]
        assert result.call_sites[0]["arguments"] == [{
            "range": _range(source, "1"),
            "source": "1",
            "syntax_kind": "expression",
            "literal_kind": None,
            "literal_value": None,
            "identifier": None,
        }]
        assert result.call_sites[0]["call_source"] == "service->handler(1)"
        assert result.call_sites[0]["callee_source"] == "service->handler"
        assert result.call_sites[0]["receiver_source"] is None
        assert result.call_sites[1]["call_source"] == "(*callback)(2)"
        assert result.call_sites[1]["arguments"][0]["source"] == "2"
        assert result.evidence_sources == [{
            "source_id": "sample.c",
            "decoded_text_sha256": hashlib.sha256(source.encode()).hexdigest(),
            "decoded_text": source,
        }]
        assert result.preprocessing_contexts == [{
            "source_id": "sample.c",
            **_configured_preprocessing("sample.c"),
        }]
    finally:
        temporary.cleanup()


def test_inactive_call_is_preserved_in_occurrence_ledger() -> None:
    source = "void run(void) { service->handler(1); (*callback)(2); }\n"
    temporary, result = _read(
        _artifact(source, second_presence="inactive"), source,
    )
    try:
        assert [site["presence_status"] for site in result.call_sites] == [
            "active", "inactive"
        ]
        assert len(result.call_sites) == 2
    finally:
        temporary.cleanup()


def test_macro_payload_is_bound_to_exact_grammar_ranges() -> None:
    source = "#define TRACE(value, ...) log_trace(value, __VA_ARGS__)\n"
    validated = validate_common_evidence(
        _macro_artifact(source), source_bytes=source.encode("utf-8"),
        expected_source_id="sample.c",
    )

    assert len(validated.macro_facts) == 1
    macro = validated.macro_facts[0]
    assert macro.terminal_name == "TRACE"
    assert macro.macro_kind == "function"
    assert macro.variadic is True
    assert source[macro.name_range.char_offset:macro.name_range.end] == "TRACE"
    assert [
        source[item.char_offset:item.end] for item in macro.parameter_ranges
    ] == ["value"]
    assert source[
        macro.replacement_range.char_offset:macro.replacement_range.end
    ] == "log_trace(value, __VA_ARGS__)"
    assert validated.configured_preprocessing == _configured_preprocessing("sample.c")


@pytest.mark.parametrize(
    ("mutate", "message"),
    (
        (
            lambda fact: fact["payload"].update(terminalName="TRAC"),
            "macro.payload.terminalName",
        ),
        (
            lambda fact: fact["payload"].update(macroKind="procedure"),
            "macro.payload.macroKind",
        ),
        (
            lambda fact: fact["payload"].update(
                nameRange={"charOffset": 0, "charLength": 6},
            ),
            "macro.payload.terminalName",
        ),
        (
            lambda fact: fact["payload"].update(
                replacementRange={"charOffset": 0, "charLength": 7},
            ),
            "macro.payload.replacementRange",
        ),
    ),
)
def test_invalid_macro_payload_fails_closed(mutate, message) -> None:
    source = "#define TRACE(value, ...) log_trace(value, __VA_ARGS__)\n"
    raw = _macro_artifact(source)
    mutate(raw["evidence"]["facts"][0])
    with pytest.raises(CommonEvidenceContractError, match=message):
        validate_common_evidence(
            raw, source_bytes=source.encode("utf-8"),
            expected_source_id="sample.c",
        )


@pytest.mark.parametrize(
    ("mutate", "message"),
    (
        (lambda raw: raw.pop("evidence"), "evidence"),
        (
            lambda raw: raw["evidence"].pop("configuredPreprocessing"),
            "configuredPreprocessing",
        ),
        (
            lambda raw: raw["evidence"]["configuredPreprocessing"]["build"].update(
                unresolvedEvidenceIds=[],
            ),
            "evidence ID populations",
        ),
        (
            lambda raw: raw["evidence"]["configuredPreprocessing"]["trace"].update(
                evidenceIds=[],
            ),
            "trace accounting",
        ),
        (
            lambda raw: raw["evidence"]["configuredPreprocessing"].update(
                status="exact", trust="authoritative",
            ),
            "build/trace closure",
        ),
        (lambda raw: raw["evidence"].update(rawSourceSha256="0" * 64), "rawSourceSha256"),
        (lambda raw: raw["evidence"]["facts"][0].update(factId="0" * 64), "factId"),
        (lambda raw: raw["evidence"]["facts"][0]["range"].update(charLength=10_000), "range"),
        (lambda raw: raw["evidence"]["completeness"][0].update(population=1), "population"),
        (
            lambda raw: raw["evidence"]["completeness"][0].update(
                population=3, explicitlyUnresolved=1,
            ),
            "complete cannot contain",
        ),
        (
            lambda raw: raw["evidence"]["completeness"][0].update(
                status="partial",
                reason="insufficient_missing_build_configuration",
                reasons=["different_reason"],
            ),
            "compatibility reason",
        ),
        (
            lambda raw: raw["evidence"]["completeness"][0].update(
                status="partial",
                reason="insufficient_missing_build_configuration",
                reasons=["insufficient_missing_build_configuration"],
                unresolvedScopeFactIds=[raw["evidence"]["facts"][0]["factId"]],
            ),
            "conditional-region fact",
        ),
        (lambda raw: raw["evidence"]["facts"][1]["payload"].update(terminalName="callback"),
         "terminalName"),
    ),
)
def test_corrupt_or_incomplete_evidence_fails_closed_before_publish(mutate, message) -> None:
    source = "void run(void) { service->handler(1); (*callback)(2); }\n"
    raw = copy.deepcopy(_artifact(source))
    mutate(raw)
    with TemporaryDirectory() as temp:
        root = Path(temp)
        (root / "sample.c").write_text(source, encoding="utf-8", newline="")
        artifact = root / "sample.json"
        artifact.write_text(json.dumps(raw), encoding="utf-8")
        with pytest.raises(CommonEvidenceContractError, match=message):
            read_antlr_json(artifact, root)


def test_v1_preprocessing_closure_cannot_authorize_raw_source_occurrences() -> None:
    source = "void run(void) { service->handler(1); (*callback)(2); }\n"
    raw = _artifact(source)
    configured = raw["evidence"]["configuredPreprocessing"]
    build_id = configured["build"]["unresolvedEvidenceIds"][0]
    trace_id = configured["trace"]["evidenceIds"][0]
    configured.update(status="exact", trust="authoritative")
    configured["build"].update(
        status="exact", population=1, emitted=1, explicitlyUnresolved=0,
        commandOccurrenceIds=[build_id], emittedCommandOccurrenceIds=[build_id],
        unresolvedCommandOccurrenceIds=[], unresolvedEvidenceIds=[], reasons=[],
    )
    configured["trace"].update(
        status="exact", population=1, emitted=1, explicitlyUnresolved=0,
        evidenceIds=[trace_id], emittedEvidenceIds=[trace_id],
        unresolvedEvidenceIds=[], reasons=[],
    )

    with pytest.raises(CommonEvidenceContractError, match="v1 cannot authorize"):
        validate_common_evidence(
            raw, source_bytes=source.encode("utf-8"),
            expected_source_id="sample.c",
        )


def test_legacy_call_population_must_match_canonical_facts_exactly_once() -> None:
    source = "void run(void) { service->handler(1); (*callback)(2); }\n"
    raw = _artifact(source)
    raw["children"][0]["children"].pop()
    with TemporaryDirectory() as temp:
        root = Path(temp)
        (root / "sample.c").write_text(source, encoding="utf-8", newline="")
        artifact = root / "sample.json"
        artifact.write_text(
            json.dumps(with_identifier_contract(raw)), encoding="utf-8",
        )
        with pytest.raises(CommonEvidenceContractError, match="call population"):
            read_antlr_json(artifact, root)


def test_parser_owned_exact_euc_kr_decode_is_reused_by_source_cache() -> None:
    source = "/* 한글 */\n"
    source_bytes = source.encode("euc-kr")
    decoded_hash = hashlib.sha256(source.encode("utf-8")).hexdigest()
    raw = {
        "type": "FILE", "fileName": "sample.c", "filePath": "sample.c",
        "children": [],
        "evidence": {
            "version": "1.0.0", "sourceId": "sample.c",
            "rawSourceSha256": hashlib.sha256(source_bytes).hexdigest(),
            "decodedTextSha256": decoded_hash, "decodedText": source,
            "sourceEncoding": "EUC-KR", "decodeStatus": "exact",
            "positionEncoding": "unicode-code-point",
                "rangeConvention": "half-open", "rangeEncoding": "char-offset-length",
                "parseStatus": "exact",
                "configuredPreprocessing": _configured_preprocessing("sample.c"),
                "facts": [], "grammarRules": [], "presences": [],
            "completeness": [
                {
                    "kind": kind,
                    "status": "complete" if kind in {"call", "conditional_region"}
                    else "unsupported",
                    "population": 0, "emitted": 0, "explicitlyUnresolved": 0,
                }
                for kind in KINDS
            ],
        },
    }
    with TemporaryDirectory() as temp:
        root = Path(temp)
        source_path = root / "sample.c"
        source_path.write_bytes(source_bytes)
        artifact = root / "sample.json"
        artifact.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
        cache = SourceFileCache()
        result = read_antlr_json(artifact, root, source_cache=cache)

        assert result.evidence_sources[0]["decoded_text"] == source
        assert cache.lines(str(source_path)) == ["/* 한글 */"]
