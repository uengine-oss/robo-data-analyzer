from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from pipeline.steps.step2_load_ast.dispatch import read_antlr_json
from pipeline.steps.step2_load_ast.evidence_ir import (
    CommonEvidenceContractError,
    validate_common_evidence,
)
from shared.files.cache import SourceFileCache


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
    artifact.write_text(json.dumps(raw), encoding="utf-8")
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
        assert result.call_sites[0]["argument_ranges"] == [
            _range(source, "1")
        ]
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
        artifact.write_text(json.dumps(raw), encoding="utf-8")
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
