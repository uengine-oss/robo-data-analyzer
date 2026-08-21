"""Strict consumer for versioned Parser common evidence IR.

The Parser owns decoding and grammar boundaries.  This module verifies that wire
contract against the exact source bytes; it does not search or reparse source.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any, Mapping


_SUPPORTED_VERSIONS = frozenset({"1.0.0", "1.1.0"})
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
_IMPORT_KINDS = frozenset({
    "source_file", "computed", "type", "namespace", "static_member",
    "module", "module_member",
})
_IMPORT_TARGET_KINDS = frozenset({"quoted", "angle", "computed", "qualified"})
_IMPORT_LOCALITIES = frozenset({"local", "system", "unspecified"})
_MACRO_KINDS = frozenset({"object", "function"})
_BUILD_STATUSES = frozenset({"exact", "partial", "unresolved"})
_PREPROCESSING_TRUST = frozenset({
    "authoritative", "compatible_replay", "unresolved",
})
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
class ImportBindingFact:
    import_kind: str
    target_range: EvidenceRange
    target_kind: str
    path_component_ranges: tuple[EvidenceRange, ...]
    member_range: EvidenceRange | None
    alias_range: EvidenceRange | None
    relative_level: int
    wildcard: bool
    locality: str


@dataclass(frozen=True)
class ImportEvidenceFact:
    fact_id: str
    source_id: str
    directive_range: EvidenceRange
    directive_kind: str
    entries: tuple[ImportBindingFact, ...]
    grammar_rule: str
    presence: Mapping[str, Any]


@dataclass(frozen=True)
class MacroEvidenceFact:
    fact_id: str
    source_id: str
    macro_range: EvidenceRange
    name_range: EvidenceRange
    macro_kind: str
    terminal_name: str
    parameter_ranges: tuple[EvidenceRange, ...]
    variadic: bool
    replacement_range: EvidenceRange | None
    grammar_rule: str
    presence: Mapping[str, Any]

    def as_record(self) -> dict[str, Any]:
        return {
            "fact_id": self.fact_id,
            "source_id": self.source_id,
            "range": self.macro_range.as_dict(),
            "name_range": self.name_range.as_dict(),
            "macro_kind": self.macro_kind,
            "terminal_name": self.terminal_name,
            "parameter_ranges": [item.as_dict() for item in self.parameter_ranges],
            "variadic": self.variadic,
            "replacement_range": (
                self.replacement_range.as_dict()
                if self.replacement_range is not None else None
            ),
            "grammar_rule": self.grammar_rule,
            "presence_status": self.presence["status"],
            "presence": dict(self.presence),
        }


@dataclass(frozen=True)
class ValidatedCommonEvidence:
    version: str
    source_id: str
    decoded_text_sha256: str
    decoded_text: str
    call_facts: tuple[CallEvidenceFact, ...]
    import_facts: tuple[ImportEvidenceFact, ...]
    macro_facts: tuple[MacroEvidenceFact, ...]
    completeness: Mapping[str, Mapping[str, Any]]
    configured_preprocessing: Mapping[str, Any] | None

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

    def preprocessing_record(self) -> dict[str, Any] | None:
        if self.configured_preprocessing is None:
            return None
        return {
            "source_id": self.source_id,
            **dict(self.configured_preprocessing),
        }


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


def _string_array(value: Any, *, field: str) -> list[str]:
    if not isinstance(value, list) or not all(
        isinstance(item, str) and item for item in value
    ):
        raise _fail(field, "nonblank string array required")
    return list(value)


def _evidence_ids(value: Any, *, field: str) -> list[str]:
    values = _string_array(value, field=field)
    if len(values) != len(set(values)) or any(
        len(item) != 64 or any(char not in "0123456789abcdef" for char in item)
        for item in values
    ):
        raise _fail(field, "unique lowercase SHA-256 IDs required")
    return values


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
) -> tuple[str, str, str, str]:
    version = evidence.get("version")
    if version not in _SUPPORTED_VERSIONS:
        raise _fail(
            "version",
            f"supported versions={sorted(_SUPPORTED_VERSIONS)!r}, got {version!r}",
        )
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
    return str(version), source_id, decoded_hash, decoded


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


def _validate_macro(
    fact: Mapping[str, Any],
    *,
    source_id: str,
    fact_range: EvidenceRange,
    grammar_rule: str,
    presence: Mapping[str, Any],
    source: str,
) -> MacroEvidenceFact:
    if fact_range.char_length == 0:
        raise _fail("macro.range", "nonempty directive range required")
    payload = fact.get("payload")
    if not isinstance(payload, dict):
        raise _fail("macro.payload", "object required")
    name_range = _range(
        payload.get("nameRange"), field="macro.payload.nameRange",
        source_length=len(source),
    )
    if (
        name_range.char_length == 0
        or name_range.char_offset < fact_range.char_offset
        or name_range.end > fact_range.end
    ):
        raise _fail("macro.payload.nameRange", "nonempty subrange of macro required")
    terminal_name = payload.get("terminalName")
    if not isinstance(terminal_name, str) or not terminal_name:
        raise _fail("macro.payload.terminalName", "nonempty grammar terminal required")
    if source[name_range.char_offset:name_range.end] != terminal_name:
        raise _fail(
            "macro.payload.terminalName",
            "must exactly equal the decoded source name range",
        )
    macro_kind = payload.get("macroKind")
    if macro_kind not in _MACRO_KINDS:
        raise _fail("macro.payload.macroKind", f"invalid value {macro_kind!r}")
    variadic = payload.get("variadic")
    if not isinstance(variadic, bool):
        raise _fail("macro.payload.variadic", "boolean required")
    parameter_values = payload.get("parameterRanges")
    if not isinstance(parameter_values, list):
        raise _fail("macro.payload.parameterRanges", "array required")
    parameters: list[EvidenceRange] = []
    previous_end = name_range.end
    for index, value in enumerate(parameter_values):
        parameter = _range(
            value, field=f"macro.payload.parameterRanges[{index}]",
            source_length=len(source),
        )
        if (
            parameter.char_length == 0
            or parameter.char_offset < previous_end
            or parameter.end > fact_range.end
        ):
            raise _fail(
                f"macro.payload.parameterRanges[{index}]",
                "ordered nonempty macro subrange after name required",
            )
        previous_end = parameter.end
        parameters.append(parameter)
    if macro_kind == "object" and (parameters or variadic):
        raise _fail(
            "macro.payload.macroKind",
            "object macro cannot declare parameters or variadic form",
        )
    replacement_value = payload.get("replacementRange")
    replacement = None
    if replacement_value is not None:
        replacement = _range(
            replacement_value, field="macro.payload.replacementRange",
            source_length=len(source),
        )
        if (
            replacement.char_offset < previous_end
            or replacement.end > fact_range.end
        ):
            raise _fail(
                "macro.payload.replacementRange",
                "macro subrange after name/parameters required",
            )
    return MacroEvidenceFact(
        fact_id=str(fact.get("factId") or ""),
        source_id=source_id,
        macro_range=fact_range,
        name_range=name_range,
        macro_kind=str(macro_kind),
        terminal_name=terminal_name,
        parameter_ranges=tuple(parameters),
        variadic=variadic,
        replacement_range=replacement,
        grammar_rule=grammar_rule,
        presence=dict(presence),
    )


def _import_subrange(
    value: Any,
    *,
    field: str,
    source_length: int,
    directive_range: EvidenceRange,
) -> EvidenceRange:
    result = _range(value, field=field, source_length=source_length)
    if (
        result.char_length == 0
        or result.char_offset < directive_range.char_offset
        or result.end > directive_range.end
    ):
        raise _fail(field, "nonempty subrange of import directive required")
    return result


def _optional_import_subrange(
    value: Any,
    *,
    field: str,
    source_length: int,
    directive_range: EvidenceRange,
) -> EvidenceRange | None:
    if value is None:
        return None
    return _import_subrange(
        value, field=field, source_length=source_length,
        directive_range=directive_range,
    )


def _validate_import_binding(
    raw: Any,
    *,
    index: int,
    source: str,
    directive_range: EvidenceRange,
) -> ImportBindingFact:
    field = f"import.payload.entries[{index}]"
    if not isinstance(raw, dict):
        raise _fail(field, "object required")
    _require_exact_fields(raw, {
        "importKind", "targetKind", "targetRange", "pathComponentRanges",
        "memberRange", "aliasRange", "relativeLevel", "wildcard", "locality",
    }, field=field)
    import_kind = raw.get("importKind")
    if import_kind not in _IMPORT_KINDS:
        raise _fail(f"{field}.importKind", f"invalid value {import_kind!r}")
    target_kind = raw.get("targetKind")
    if target_kind not in _IMPORT_TARGET_KINDS:
        raise _fail(f"{field}.targetKind", f"invalid value {target_kind!r}")
    target_range = _import_subrange(
        raw.get("targetRange"), field=f"{field}.targetRange",
        source_length=len(source), directive_range=directive_range,
    )
    raw_components = raw.get("pathComponentRanges")
    if not isinstance(raw_components, list):
        raise _fail(f"{field}.pathComponentRanges", "array required")
    components: list[EvidenceRange] = []
    previous_end = -1
    for component_index, raw_component in enumerate(raw_components):
        component = _import_subrange(
            raw_component,
            field=f"{field}.pathComponentRanges[{component_index}]",
            source_length=len(source), directive_range=directive_range,
        )
        if component.char_offset < previous_end:
            raise _fail(
                f"{field}.pathComponentRanges[{component_index}]",
                "ordered non-overlapping ranges required",
            )
        previous_end = component.end
        components.append(component)
    member = _optional_import_subrange(
        raw.get("memberRange"), field=f"{field}.memberRange",
        source_length=len(source), directive_range=directive_range,
    )
    alias = _optional_import_subrange(
        raw.get("aliasRange"), field=f"{field}.aliasRange",
        source_length=len(source), directive_range=directive_range,
    )
    relative_level = _integer(
        raw.get("relativeLevel"), field=f"{field}.relativeLevel",
    )
    if relative_level < 0:
        raise _fail(f"{field}.relativeLevel", "non-negative integer required")
    wildcard = raw.get("wildcard")
    if not isinstance(wildcard, bool):
        raise _fail(f"{field}.wildcard", "boolean required")
    locality = raw.get("locality")
    if locality not in _IMPORT_LOCALITIES:
        raise _fail(f"{field}.locality", f"invalid value {locality!r}")

    paths_inside_target = all(
        target_range.char_offset <= component.char_offset
        and component.end <= target_range.end
        for component in components
    )
    member_inside_target = (
        member is None
        or target_range.char_offset <= member.char_offset <= member.end <= target_range.end
    )
    alias_inside_target = (
        alias is None
        or target_range.char_offset <= alias.char_offset <= alias.end <= target_range.end
    )
    if import_kind == "computed":
        valid = (
            target_kind == "computed" and not components and member is None
            and alias is None and not wildcard and relative_level == 0
            and locality == "unspecified"
        )
    elif import_kind == "source_file":
        expected_locality = "local" if target_kind == "quoted" else "system"
        delimiters = ('"', '"') if target_kind == "quoted" else ("<", ">")
        target = source[target_range.char_offset:target_range.end]
        valid = (
            target_kind in {"quoted", "angle"} and len(components) == 1
            and member is None and alias is None and not wildcard
            and relative_level == 0 and locality == expected_locality
            and target.startswith(delimiters[0]) and target.endswith(delimiters[1])
            and components[0].char_offset == target_range.char_offset + 1
            and components[0].end == target_range.end - 1
        )
    elif import_kind == "type":
        valid = (
            target_kind == "qualified" and bool(components) and paths_inside_target
            and member is None and alias is None and not wildcard
            and relative_level == 0 and locality == "unspecified"
        )
    elif import_kind == "namespace":
        valid = (
            target_kind == "qualified" and bool(components) and paths_inside_target
            and member is None and alias is None and wildcard
            and relative_level == 0 and locality == "unspecified"
        )
    elif import_kind == "static_member":
        valid = (
            target_kind == "qualified" and bool(components) and paths_inside_target
            and alias is None and (wildcard != (member is not None))
            and member_inside_target and relative_level == 0
            and locality == "unspecified"
        )
    elif import_kind == "module":
        valid = (
            target_kind == "qualified" and bool(components) and paths_inside_target
            and member is None and not wildcard and relative_level == 0
            and alias_inside_target and locality == "unspecified"
        )
    else:
        valid = (
            target_kind == "qualified" and (wildcard != (member is not None))
            and member_inside_target and alias_inside_target
            and (alias is None or member is not None)
            and locality == "unspecified"
        )
    if not valid:
        raise _fail(field, f"invalid {import_kind} binding contract")
    return ImportBindingFact(
        import_kind=str(import_kind), target_range=target_range,
        target_kind=str(target_kind), path_component_ranges=tuple(components),
        member_range=member, alias_range=alias, relative_level=relative_level,
        wildcard=wildcard, locality=str(locality),
    )


def _validate_import(
    fact: Mapping[str, Any],
    *,
    version: str,
    source_id: str,
    fact_range: EvidenceRange,
    grammar_rule: str,
    presence: Mapping[str, Any],
    source: str,
) -> ImportEvidenceFact:
    if fact_range.char_length == 0:
        raise _fail("import.range", "nonempty directive range required")
    payload = fact.get("payload")
    if not isinstance(payload, dict):
        raise _fail("import.payload", "object required")
    if version == "1.0.0":
        _require_exact_fields(payload, {
            "directiveKind", "targetKind", "targetRange",
        }, field="import.payload")
    else:
        _require_exact_fields(payload, {
            "directiveKind", "entries",
        }, field="import.payload")
    directive_kind = payload.get("directiveKind")
    if directive_kind not in {"include", "import"}:
        raise _fail(
            "import.payload.directiveKind",
            f"invalid value {directive_kind!r}",
        )
    if version == "1.0.0":
        if directive_kind != "include":
            raise _fail(
                "import.payload.directiveKind",
                "v1.0 import evidence only supports include",
            )
        target_kind = payload.get("targetKind")
        if target_kind not in {"quoted", "angle", "computed"}:
            raise _fail(
                "import.payload.targetKind", f"invalid value {target_kind!r}",
            )
        target_range = _import_subrange(
            payload.get("targetRange"), field="import.payload.targetRange",
            source_length=len(source), directive_range=fact_range,
        )
        entries = (ImportBindingFact(
            import_kind="computed" if target_kind == "computed" else "source_file",
            target_range=target_range, target_kind=str(target_kind),
            path_component_ranges=(), member_range=None, alias_range=None,
            relative_level=0, wildcard=False,
            locality={"quoted": "local", "angle": "system"}.get(
                str(target_kind), "unspecified",
            ),
        ),)
    else:
        raw_entries = payload.get("entries")
        if not isinstance(raw_entries, list) or not raw_entries:
            raise _fail("import.payload.entries", "nonempty array required")
        entries = tuple(
            _validate_import_binding(
                raw, index=index, source=source, directive_range=fact_range,
            )
            for index, raw in enumerate(raw_entries)
        )
        allowed = {"source_file", "computed"} if directive_kind == "include" else {
            "type", "namespace", "static_member", "module", "module_member",
        }
        if any(entry.import_kind not in allowed for entry in entries):
            raise _fail(
                "import.payload.entries",
                f"{directive_kind} contains an incompatible binding kind",
            )
    return ImportEvidenceFact(
        fact_id=str(fact.get("factId") or ""),
        source_id=source_id,
        directive_range=fact_range,
        directive_kind=str(directive_kind),
        entries=entries,
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


def _require_exact_fields(
    value: Mapping[str, Any], expected: set[str], *, field: str,
) -> None:
    actual = set(value)
    if actual != expected:
        raise _fail(
            field,
            f"exact fields required; missing={sorted(expected - actual)}, "
            f"unexpected={sorted(actual - expected)}",
        )


def _validate_build_context(value: Any) -> dict[str, Any]:
    field = "configuredPreprocessing.build"
    if not isinstance(value, dict):
        raise _fail(field, "object required")
    _require_exact_fields(value, {
        "status", "population", "emitted", "explicitlyUnresolved",
        "commandOccurrenceIds", "emittedCommandOccurrenceIds",
        "unresolvedCommandOccurrenceIds", "unresolvedEvidenceIds", "reasons",
    }, field=field)
    status = value.get("status")
    if status not in _BUILD_STATUSES:
        raise _fail(f"{field}.status", f"invalid {status!r}")
    population = _integer(value.get("population"), field=f"{field}.population")
    emitted = _integer(value.get("emitted"), field=f"{field}.emitted")
    unresolved = _integer(
        value.get("explicitlyUnresolved"),
        field=f"{field}.explicitlyUnresolved",
    )
    if min(population, emitted, unresolved) < 0 or population != emitted + unresolved:
        raise _fail(
            f"{field}.population",
            f"expected emitted + explicitlyUnresolved, got "
            f"{population}!={emitted}+{unresolved}",
        )
    command_ids = _evidence_ids(
        value.get("commandOccurrenceIds"), field=f"{field}.commandOccurrenceIds",
    )
    emitted_ids = _evidence_ids(
        value.get("emittedCommandOccurrenceIds"),
        field=f"{field}.emittedCommandOccurrenceIds",
    )
    unresolved_command_ids = _evidence_ids(
        value.get("unresolvedCommandOccurrenceIds"),
        field=f"{field}.unresolvedCommandOccurrenceIds",
    )
    unresolved_ids = _evidence_ids(
        value.get("unresolvedEvidenceIds"),
        field=f"{field}.unresolvedEvidenceIds",
    )
    if len(emitted_ids) != emitted or len(unresolved_ids) != unresolved:
        raise _fail(field, "evidence ID populations do not match accounting")
    if set(command_ids) != set(emitted_ids) | set(unresolved_command_ids):
        raise _fail(field, "command occurrence IDs are not an exact emitted/unresolved partition")
    if not set(unresolved_command_ids).issubset(unresolved_ids):
        raise _fail(field, "unresolved command IDs must be unresolved evidence IDs")
    reasons = _string_array(value.get("reasons"), field=f"{field}.reasons")
    expected_status = (
        "exact" if emitted > 0 and unresolved == 0
        else "partial" if emitted > 0 and unresolved > 0
        else "unresolved"
    )
    if status != expected_status:
        raise _fail(
            f"{field}.status",
            f"accounting requires {expected_status!r}, got {status!r}",
        )
    if (status == "exact") != (not reasons):
        raise _fail(field, "exact requires no reasons; non-exact requires reasons")
    return {
        "status": status,
        "population": population,
        "emitted": emitted,
        "explicitlyUnresolved": unresolved,
        "commandOccurrenceIds": command_ids,
        "emittedCommandOccurrenceIds": emitted_ids,
        "unresolvedCommandOccurrenceIds": unresolved_command_ids,
        "unresolvedEvidenceIds": unresolved_ids,
        "reasons": reasons,
    }


def _validate_trace_context(value: Any) -> dict[str, Any]:
    field = "configuredPreprocessing.trace"
    if not isinstance(value, dict):
        raise _fail(field, "object required")
    _require_exact_fields(value, {
        "status", "population", "emitted", "explicitlyUnresolved",
        "evidenceIds", "emittedEvidenceIds", "unresolvedEvidenceIds", "reasons",
    }, field=field)
    status = value.get("status")
    if status not in _BUILD_STATUSES:
        raise _fail(f"{field}.status", f"invalid {status!r}")
    population = _integer(value.get("population"), field=f"{field}.population")
    emitted = _integer(value.get("emitted"), field=f"{field}.emitted")
    unresolved = _integer(
        value.get("explicitlyUnresolved"),
        field=f"{field}.explicitlyUnresolved",
    )
    ids = _evidence_ids(value.get("evidenceIds"), field=f"{field}.evidenceIds")
    emitted_ids = _evidence_ids(
        value.get("emittedEvidenceIds"), field=f"{field}.emittedEvidenceIds",
    )
    unresolved_ids = _evidence_ids(
        value.get("unresolvedEvidenceIds"), field=f"{field}.unresolvedEvidenceIds",
    )
    reasons = _string_array(value.get("reasons"), field=f"{field}.reasons")
    if (
        min(population, emitted, unresolved) < 0
        or population != emitted + unresolved
        or population != len(ids)
        or emitted != len(emitted_ids)
        or unresolved != len(unresolved_ids)
    ):
        raise _fail(field, "trace accounting and evidence ID population differ")
    if set(ids) != set(emitted_ids) | set(unresolved_ids):
        raise _fail(field, "trace evidence IDs are not an exact emitted/unresolved partition")
    expected_status = (
        "exact" if emitted > 0 and unresolved == 0
        else "partial" if emitted > 0 and unresolved > 0
        else "unresolved"
    )
    if status != expected_status:
        raise _fail(
            f"{field}.status",
            f"accounting requires {expected_status!r}, got {status!r}",
        )
    if (status == "exact") != (not reasons):
        raise _fail(field, "exact requires no reasons; non-exact requires reasons")
    return {
        "status": status,
        "population": population,
        "emitted": emitted,
        "explicitlyUnresolved": unresolved,
        "evidenceIds": ids,
        "emittedEvidenceIds": emitted_ids,
        "unresolvedEvidenceIds": unresolved_ids,
        "reasons": reasons,
    }


def _validate_configured_preprocessing(
    evidence: Mapping[str, Any], *, source_id: str,
) -> dict[str, Any] | None:
    value = evidence.get("configuredPreprocessing")
    is_c_source = PurePosixPath(source_id).suffix.lower() in {".c", ".h"}
    if value is None:
        if is_c_source:
            raise _fail(
                "configuredPreprocessing",
                "C source requires explicit build and preprocessing trace closure",
            )
        return None
    if not is_c_source:
        raise _fail(
            "configuredPreprocessing",
            "C preprocessing contract is forbidden for non-C source",
        )
    if not isinstance(value, dict):
        raise _fail("configuredPreprocessing", "object required")
    _require_exact_fields(value, {"version", "status", "trust", "build", "trace"},
                          field="configuredPreprocessing")
    if value.get("version") != "1.0.0":
        raise _fail("configuredPreprocessing.version", "expected '1.0.0'")
    status = value.get("status")
    trust = value.get("trust")
    if status not in _BUILD_STATUSES:
        raise _fail("configuredPreprocessing.status", f"invalid {status!r}")
    if trust not in _PREPROCESSING_TRUST:
        raise _fail("configuredPreprocessing.trust", f"invalid {trust!r}")
    build = _validate_build_context(value.get("build"))
    trace = _validate_trace_context(value.get("trace"))

    if build["status"] != "exact":
        expected_status, expected_trust = "unresolved", "unresolved"
    elif trace["status"] == "unresolved":
        expected_status, expected_trust = "unresolved", "unresolved"
    elif trust == "compatible_replay":
        expected_status, expected_trust = "partial", "compatible_replay"
    elif trace["status"] == "exact":
        expected_status, expected_trust = "exact", "authoritative"
    else:
        expected_status, expected_trust = "partial", "authoritative"
    if (status, trust) != (expected_status, expected_trust):
        raise _fail(
            "configuredPreprocessing",
            f"build/trace closure requires status={expected_status!r}, "
            f"trust={expected_trust!r}; got status={status!r}, trust={trust!r}",
        )
    if status == "exact":
        raise _fail(
            "configuredPreprocessing",
            "v1 cannot authorize raw-source semantic occurrences; "
            "a v2 configured semantic occurrence contract is required",
        )
    return {
        "version": "1.0.0",
        "status": status,
        "trust": trust,
        "build": build,
        "trace": trace,
    }


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
    version, source_id, decoded_hash, source = _validate_envelope(
        evidence, source_bytes=source_bytes, expected_source_id=expected,
    )
    configured_preprocessing = _validate_configured_preprocessing(
        evidence, source_id=source_id,
    )
    grammar_rules, presences = _validate_tables(evidence)
    facts = evidence.get("facts")
    if not isinstance(facts, list):
        raise _fail("facts", "array required")

    ordinals: dict[tuple[str, int, int], int] = {}
    fact_kinds: dict[str, str] = {}
    emitted_by_kind = {kind: 0 for kind in _KINDS}
    calls: list[CallEvidenceFact] = []
    imports: list[ImportEvidenceFact] = []
    macros: list[MacroEvidenceFact] = []
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
        elif kind == "import":
            imports.append(_validate_import(
                fact,
                version=version,
                source_id=source_id,
                fact_range=fact_range,
                grammar_rule=grammar_rule,
                presence=presence,
                source=source,
            ))
        elif kind == "macro":
            macros.append(_validate_macro(
                fact,
                source_id=source_id,
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
        version=version,
        source_id=source_id,
        decoded_text_sha256=decoded_hash,
        decoded_text=source,
        call_facts=tuple(calls),
        import_facts=tuple(imports),
        macro_facts=tuple(macros),
        completeness=completeness,
        configured_preprocessing=configured_preprocessing,
    )
