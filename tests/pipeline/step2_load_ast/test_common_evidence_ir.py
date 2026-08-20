from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from pipeline.steps.step2_load_ast.dispatch import read_antlr_json
from pipeline.steps.step2_load_ast.evidence_ir import CommonEvidenceContractError
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


@pytest.mark.parametrize(
    ("mutate", "message"),
    (
        (lambda raw: raw.pop("evidence"), "evidence"),
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
            "parseStatus": "exact", "facts": [], "grammarRules": [], "presences": [],
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
