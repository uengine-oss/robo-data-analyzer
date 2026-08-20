"""Strict consumer for Parser common evidence IR v1.

The Parser owns decoding and grammar boundaries.  This module verifies that wire
contract against the exact source bytes; it does not search or reparse source.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any, Mapping


_VERSION = "1.0.0"
_ID_DOMAIN = "robo-evidence-v1"
_KINDS = (
    "call", "import", "symbol", "literal", "assignment", "parameter",
    "macro", "embedded_language", "conditional_region",
)
_COMPLETENESS_STATUSES = frozenset({
    "complete", "not_applicable", "partial", "unsupported",
})
_PRESENCE_STATUSES = frozenset({
    "active", "inactive", "conditional", "unknown",
})
_CALLEE_KINDS = frozenset({"named", "constructor", "expression"})
_ENCODINGS = {
    "UTF-8": "utf-8",
    "EUC-KR": "euc_kr",
    "MS949": "cp949",
    "WINDOWS-949": "cp949",
    "X-WINDOWS-949": "cp949",
}


class CommonEvidenceContractError(ValueError):
    """Parser evidence cannot be consumed without inventing or losing facts."""


@dataclass(frozen=True)
class EvidenceRange:
    char_offset: int
    char_length: int

    @property
    def end(self) -> int:
        return self.char_offset + self.char_length

    def as_dict(self) -> dict[str, int]:
        return {"charOffset": self.char_offset, "charLength": self.char_length}


@dataclass(frozen=True)
class CallEvidenceFact:
    fact_id: str
    call_range: EvidenceRange
    callee_range: EvidenceRange
    callee_kind: str
    terminal_name: str | None
    argument_ranges: tuple[EvidenceRange, ...]
    grammar_rule: str
    presence: Mapping[str, Any]


@dataclass(frozen=True)
class ValidatedCommonEvidence:
    source_id: str
    decoded_text_sha256: str
    decoded_text: str
    call_facts: tuple[CallEvidenceFact, ...]
    completeness: Mapping[str, Mapping[str, Any]]

    def source_record(self) -> dict[str, str]:
        return {
            "source_id": self.source_id,
            "decoded_text_sha256": self.decoded_text_sha256,
            "decoded_text": self.decoded_text,
        }

    def completeness_records(self) -> list[dict[str, Any]]:
        return [
            {"source_id": self.source_id, **dict(self.completeness[kind])}
            for kind in _KINDS
        ]


def _fail(field: str, detail: str) -> CommonEvidenceContractError:
    return CommonEvidenceContractError(f"common evidence {field}: {detail}")


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _source_id(value: Any, *, field: str) -> str:
    normalized = str(value or "").strip().replace("\\", "/")
    path = PurePosixPath(normalized)
    if (
        not normalized
        or path.is_absolute()
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise _fail(field, f"normalized relative path required, got {value!r}")
    return path.as_posix()


def _integer(value: Any, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise _fail(field, f"integer required, got {value!r}")
    return value


def _range(value: Any, *, field: str, source_length: int) -> EvidenceRange:
    if not isinstance(value, dict):
        raise _fail(field, "range object required")
    offset = _integer(value.get("charOffset"), field=f"{field}.charOffset")
    length = _integer(value.get("charLength"), field=f"{field}.charLength")
    if offset < 0 or length < 0 or offset + length > source_length:
        raise _fail(
            field,
            f"range exceeds decoded source: offset={offset}, length={length}, "
            f"sourceLength={source_length}",
        )
    return EvidenceRange(offset, length)


def _table_ref(value: Any, table: list[Any], *, field: str) -> Any:
    index = _integer(value, field=field)
    if index < 0 or index >= len(table):
        raise _fail(field, f"table index out of bounds: {index}/{len(table)}")
    return table[index]


def _fact_id(
    source_id: str,
    decoded_hash: str,
    kind: str,
    fact_range: EvidenceRange,
    ordinal: int,
) -> str:
    value = "\0".join((
        _ID_DOMAIN,
        source_id,
        decoded_hash,
        kind,
        f"{fact_range.char_offset}:{fact_range.char_length}",
        str(ordinal),
    ))
    return _sha256(value.encode("utf-8"))


def _validate_envelope(
    evidence: Mapping[str, Any],
    *,
    source_bytes: bytes,
    expected_source_id: str,
) -> tuple[str, str, str]:
    if evidence.get("version") != _VERSION:
        raise _fail("version", f"expected {_VERSION!r}, got {evidence.get('version')!r}")
    source_id = _source_id(evidence.get("sourceId"), field="sourceId")
    if source_id.startswith("unscoped/") or source_id != expected_source_id:
        raise _fail(
            "sourceId",
            f"canonical source mismatch: expected={expected_source_id!r}, got={source_id!r}",
        )

    raw_hash = str(evidence.get("rawSourceSha256") or "").lower()
    actual_raw_hash = _sha256(source_bytes)
    if raw_hash != actual_raw_hash:
        raise _fail(
            "rawSourceSha256",
            f"expected source hash {actual_raw_hash}, got {raw_hash!r}",
        )
    if evidence.get("decodeStatus") != "exact":
        raise _fail("decodeStatus", "canonical Analyzer consumption requires exact decode")
    encoding_name = str(evidence.get("sourceEncoding") or "").upper()
    codec = _ENCODINGS.get(encoding_name)
    if codec is None:
        raise _fail("sourceEncoding", f"unsupported exact charset {encoding_name!r}")
    try:
        decoded = source_bytes.decode(codec, errors="strict")
    except UnicodeDecodeError as error:
        raise _fail("sourceEncoding", f"strict decode failed: {error}") from error
    declared_text = evidence.get("decodedText")
    if not isinstance(declared_text, str) or declared_text != decoded:
        raise _fail("decodedText", "does not equal strict decode of bound source bytes")
    decoded_hash = _sha256(decoded.encode("utf-8"))
    if str(evidence.get("decodedTextSha256") or "").lower() != decoded_hash:
        raise _fail("decodedTextSha256", f"expected {decoded_hash}")

    fixed = {
        "positionEncoding": "unicode-code-point",
        "rangeConvention": "half-open",
        "rangeEncoding": "char-offset-length",
    }
    for field, expected in fixed.items():
        if evidence.get(field) != expected:
            raise _fail(field, f"expected {expected!r}, got {evidence.get(field)!r}")
    if evidence.get("parseStatus") not in {"exact", "recovered", "partial", "unresolved"}:
        raise _fail("parseStatus", f"invalid value {evidence.get('parseStatus')!r}")
    return source_id, decoded_hash, decoded


def _validate_tables(evidence: Mapping[str, Any]) -> tuple[list[str], list[dict[str, Any]]]:
    grammar_rules = evidence.get("grammarRules")
    if not isinstance(grammar_rules, list) or not all(
        isinstance(rule, str) and rule.strip() for rule in grammar_rules
    ):
        raise _fail("grammarRules", "nonblank string table required")
    if len(grammar_rules) != len(set(grammar_rules)):
        raise _fail("grammarRules", "duplicate table entry")
    presences = evidence.get("presences")
    if not isinstance(presences, list):
        raise _fail("presences", "array required")
    for index, presence in enumerate(presences):
        if not isinstance(presence, dict):
            raise _fail(f"presences[{index}]", "object required")
        if presence.get("status") not in _PRESENCE_STATUSES:
            raise _fail(
                f"presences[{index}].status", f"invalid value {presence.get('status')!r}",
            )
        if not isinstance(presence.get("provenance"), str) or not presence["provenance"]:
            raise _fail(f"presences[{index}].provenance", "nonblank string required")
    return grammar_rules, presences


def _validate_call(
    fact: Mapping[str, Any],
    *,
    fact_range: EvidenceRange,
    grammar_rule: str,
    presence: Mapping[str, Any],
    source: str,
) -> CallEvidenceFact:
    if fact_range.char_length == 0:
        raise _fail("call.range", "nonempty call range required")
    payload = fact.get("payload")
    if not isinstance(payload, dict):
        raise _fail("call.payload", "object required")
    callee_range = _range(
        payload.get("calleeRange"), field="call.payload.calleeRange",
        source_length=len(source),
    )
    if (
        callee_range.char_length == 0
        or callee_range.char_offset < fact_range.char_offset
        or callee_range.end > fact_range.end
    ):
        raise _fail("call.payload.calleeRange", "nonempty subrange of call required")
    callee_kind = payload.get("calleeKind")
    if callee_kind not in _CALLEE_KINDS:
        raise _fail("call.payload.calleeKind", f"invalid value {callee_kind!r}")
    terminal_name = payload.get("terminalName")
    if callee_kind == "expression":
        if terminal_name is not None:
            raise _fail("call.payload.terminalName", "expression callee requires null")
    elif not isinstance(terminal_name, str) or not terminal_name.strip():
        raise _fail(
            "call.payload.terminalName", f"{callee_kind} callee requires a name",
        )
    elif terminal_name not in source[callee_range.char_offset:callee_range.end]:
        raise _fail(
            "call.payload.terminalName",
            "grammar terminal is not bound by the exact callee range",
        )
    argument_values = payload.get("argumentRanges")
    if not isinstance(argument_values, list):
        raise _fail("call.payload.argumentRanges", "array required")
    arguments: list[EvidenceRange] = []
    previous_end = fact_range.char_offset
    for index, value in enumerate(argument_values):
        argument = _range(
            value, field=f"call.payload.argumentRanges[{index}]",
            source_length=len(source),
        )
        if (
            argument.char_offset < fact_range.char_offset
            or argument.end > fact_range.end
            or argument.char_offset < previous_end
        ):
            raise _fail(
                f"call.payload.argumentRanges[{index}]",
                "ordered non-overlapping call subrange required",
            )
        previous_end = argument.end
        arguments.append(argument)
    return CallEvidenceFact(
        fact_id=str(fact.get("factId") or ""),
        call_range=fact_range,
        callee_range=callee_range,
        callee_kind=str(callee_kind),
        terminal_name=terminal_name,
        argument_ranges=tuple(arguments),
        grammar_rule=grammar_rule,
        presence=dict(presence),
    )


def _validate_completeness(
    evidence: Mapping[str, Any],
    *,
    emitted_by_kind: Mapping[str, int],
    fact_kinds: Mapping[str, str],
) -> dict[str, dict[str, Any]]:
    rows = evidence.get("completeness")
    if not isinstance(rows, list):
        raise _fail("completeness", "array required")
    result: dict[str, dict[str, Any]] = {}
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise _fail(f"completeness[{index}]", "object required")
        kind = row.get("kind")
        if kind not in _KINDS or kind in result:
            raise _fail(f"completeness[{index}].kind", f"invalid/duplicate {kind!r}")
        status = row.get("status")
        if status not in _COMPLETENESS_STATUSES:
            raise _fail(f"completeness[{index}].status", f"invalid {status!r}")
        population = _integer(row.get("population"), field=f"completeness[{kind}].population")
        emitted = _integer(row.get("emitted"), field=f"completeness[{kind}].emitted")
        unresolved = _integer(
            row.get("explicitlyUnresolved"),
            field=f"completeness[{kind}].explicitlyUnresolved",
        )
        if min(population, emitted, unresolved) < 0 or population != emitted + unresolved:
            raise _fail(
                f"completeness[{kind}].population",
                f"expected emitted + explicitlyUnresolved, got {population}!={emitted}+{unresolved}",
            )
        if emitted != emitted_by_kind.get(str(kind), 0):
            raise _fail(
                f"completeness[{kind}].emitted",
                f"declared={emitted}, actual facts={emitted_by_kind.get(str(kind), 0)}",
            )
        if status in {"unsupported", "not_applicable"} and population:
            raise _fail(f"completeness[{kind}]", f"{status} cannot claim a population")
        if status == "complete" and unresolved:
            raise _fail(
                f"completeness[{kind}]",
                "complete cannot contain explicitly unresolved population",
            )
        reasons_value = row.get("reasons", [])
        if not isinstance(reasons_value, list) or not all(
            isinstance(reason, str) and reason for reason in reasons_value
        ):
            raise _fail(f"completeness[{kind}].reasons", "string array required")
        if status == "partial" and not reasons_value and not row.get("reason"):
            raise _fail(f"completeness[{kind}]", "partial requires an explicit reason")
        compatibility_reason = row.get("reason")
        if compatibility_reason is not None and (
            not isinstance(compatibility_reason, str) or not compatibility_reason
        ):
            raise _fail(f"completeness[{kind}].reason", "nonblank string required")
        if reasons_value and compatibility_reason != reasons_value[0]:
            raise _fail(
                f"completeness[{kind}].reason",
                "compatibility reason must equal the first reasons entry",
            )
        unresolved_scopes = row.get("unresolvedScopeFactIds", [])
        if (
            not isinstance(unresolved_scopes, list)
            or len(unresolved_scopes) != len(set(unresolved_scopes))
            or not all(
                isinstance(item, str) and fact_kinds.get(item) == "conditional_region"
                for item in unresolved_scopes
            )
        ):
            raise _fail(
                f"completeness[{kind}].unresolvedScopeFactIds",
                "every scope ID must reference a conditional-region fact in this envelope",
            )
        result[str(kind)] = dict(row)
    missing = [kind for kind in _KINDS if kind not in result]
    if missing:
        raise _fail("completeness", f"missing kinds {missing}")
    return result


def validate_common_evidence(
    raw: Mapping[str, Any],
    *,
    source_bytes: bytes,
    expected_source_id: str,
) -> ValidatedCommonEvidence:
    """Validate one FILE envelope and return immutable, language-neutral call facts."""
    evidence = raw.get("evidence")
    if not isinstance(evidence, dict):
        raise _fail("evidence", "FILE root requires a v1 object")
    expected = _source_id(expected_source_id, field="expectedSourceId")
    source_id, decoded_hash, source = _validate_envelope(
        evidence, source_bytes=source_bytes, expected_source_id=expected,
    )
    grammar_rules, presences = _validate_tables(evidence)
    facts = evidence.get("facts")
    if not isinstance(facts, list):
        raise _fail("facts", "array required")

    ordinals: dict[tuple[str, int, int], int] = {}
    fact_kinds: dict[str, str] = {}
    emitted_by_kind = {kind: 0 for kind in _KINDS}
    calls: list[CallEvidenceFact] = []
    for index, fact in enumerate(facts):
        if not isinstance(fact, dict):
            raise _fail(f"facts[{index}]", "object required")
        kind = fact.get("kind")
        if kind not in _KINDS:
            raise _fail(f"facts[{index}].kind", f"unsupported value {kind!r}")
        fact_range = _range(
            fact.get("range"), field=f"facts[{index}].range", source_length=len(source),
        )
        ordinal_key = (str(kind), fact_range.char_offset, fact_range.char_length)
        ordinal = ordinals.get(ordinal_key, 0)
        ordinals[ordinal_key] = ordinal + 1
        expected_id = _fact_id(source_id, decoded_hash, str(kind), fact_range, ordinal)
        actual_id = str(fact.get("factId") or "")
        if actual_id != expected_id:
            raise _fail(
                f"facts[{index}].factId", f"expected={expected_id}, got={actual_id!r}",
            )
        if actual_id in fact_kinds:
            raise _fail(f"facts[{index}].factId", "duplicate canonical identity")
        fact_kinds[actual_id] = str(kind)
        grammar_rule = _table_ref(
            fact.get("grammarRuleRef"), grammar_rules,
            field=f"facts[{index}].grammarRuleRef",
        )
        presence = _table_ref(
            fact.get("presenceRef"), presences,
            field=f"facts[{index}].presenceRef",
        )
        emitted_by_kind[str(kind)] += 1
        if kind == "call":
            calls.append(_validate_call(
                fact,
                fact_range=fact_range,
                grammar_rule=grammar_rule,
                presence=presence,
                source=source,
            ))

    completeness = _validate_completeness(
        evidence,
        emitted_by_kind=emitted_by_kind,
        fact_kinds=fact_kinds,
    )
    return ValidatedCommonEvidence(
        source_id=source_id,
        decoded_text_sha256=decoded_hash,
        decoded_text=source,
        call_facts=tuple(calls),
        completeness=completeness,
    )
