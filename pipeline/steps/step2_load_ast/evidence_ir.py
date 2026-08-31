"""Strict consumer for versioned Parser common evidence IR.

The Parser owns decoding and grammar boundaries.  This module verifies that wire
contract against the exact source bytes; it does not search or reparse source.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any, Mapping


_SUPPORTED_VERSIONS = frozenset({"1.0.0", "1.1.0", "1.2.0", "2.0.0"})
_ID_DOMAIN = "robo-evidence-v1"
_LEGACY_KINDS = (
    "call", "import", "symbol", "literal", "assignment", "parameter",
    "macro", "embedded_language", "conditional_region",
)
_FACT_KINDS = (
    "call", "callable", "import", "symbol", "literal", "assignment",
    "parameter", "binding_target", "scope_directive", "structural_expression", "macro", "embedded_language",
    "conditional_region",
)
_V2_COMPLETENESS_KINDS = tuple(
    kind for kind in _FACT_KINDS if kind != "structural_expression"
)
_V2_OPTIONAL_COMPLETENESS_KINDS = ("structural_expression",)
_COMPLETENESS_STATUSES = frozenset({
    "complete", "not_applicable", "partial", "unsupported",
})
_PRESENCE_STATUSES = frozenset({
    "active", "inactive", "conditional", "unknown",
})
_CALLEE_KINDS = frozenset({"named", "constructor", "expression"})
_CALLABLE_ROLES = frozenset({"declaration", "definition"})
_STRUCTURAL_EXPRESSION_ROLES = frozenset({
    "condition", "case_value", "return_value",
    "assignment_target", "assignment_value", "initializer_value",
    "update_expression",
})
_SCOPE_KINDS = frozenset({
    "translation_unit", "package", "class", "function", "lambda", "comprehension", "block",
    "function_prototype",
})
_BINDING_TARGET_CONTEXTS = frozenset({
    "assignment", "annotated_assignment", "augmented_assignment",
    "for_target", "with_target", "except_target", "comprehension_target",
    "function_definition", "class_definition", "delete_target",
})
_SCOPE_DIRECTIVE_KINDS = frozenset({"global", "nonlocal"})
_IR2_FORBIDDEN_SEMANTIC_FIELDS = frozenset({
    "binding", "adapterSchema", "bindingKey", "compatibilityKey",
    "compatibilityStatus", "compatibilityScope", "definitionStatus",
    "targetScope", "resolutionMode", "externalIdentityKey",
    "type", "linkage", "meaning", "valueKind", "operatorMeaning",
    "resolvedTarget", "resolutionStatus",
})
_IMPORT_KINDS = frozenset({
    "source_file", "computed", "type", "namespace", "static_member",
    "module", "module_member",
})
_IMPORT_TARGET_KINDS = frozenset({"quoted", "angle", "computed", "qualified"})
_IMPORT_LOCALITIES = frozenset({"local", "system", "unspecified"})
_MACRO_KINDS = frozenset({"object", "function"})
_SYMBOL_KINDS = frozenset({"typedef_name", "ordinary_identifier"})
_SYMBOL_SCOPES = frozenset({"file", "block", "function_prototype"})
_SYMBOL_DECISIONS = frozenset({"type_name", "ordinary_identifier"})
_SYMBOL_PROVENANCE = frozenset({
    "source_declaration", "configured_preprocessing", "grammar_context",
    "unresolved_environment",
})
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
class ScopeEvidence:
    kind: str
    range: EvidenceRange

    def as_record(self) -> dict[str, Any]:
        return {"kind": self.kind, "range": self.range.as_dict()}


@dataclass(frozen=True)
class SyntaxTokenEvidence:
    token_kind: str
    range: EvidenceRange
    text: str

    def as_record(self) -> dict[str, Any]:
        return {
            "token_kind": self.token_kind,
            "range": self.range.as_dict(),
            "text": self.text,
        }


@dataclass(frozen=True)
class SyntaxComponentEvidence:
    grammar_rule: str
    range: EvidenceRange
    direct_tokens: tuple[SyntaxTokenEvidence, ...]
    children: tuple["SyntaxComponentEvidence", ...]

    def as_record(self) -> dict[str, Any]:
        return {
            "grammar_rule": self.grammar_rule,
            "range": self.range.as_dict(),
            "direct_tokens": [item.as_record() for item in self.direct_tokens],
            "children": [item.as_record() for item in self.children],
        }


@dataclass(frozen=True)
class CallableSyntaxEvidence:
    schema: str
    declaration_specifiers: tuple[SyntaxComponentEvidence, ...]
    declarator: SyntaxComponentEvidence
    attributes: tuple[SyntaxComponentEvidence, ...]

    def as_record(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "declaration_specifiers": [
                item.as_record() for item in self.declaration_specifiers
            ],
            "declarator": self.declarator.as_record(),
            "attributes": [item.as_record() for item in self.attributes],
        }


@dataclass(frozen=True)
class CallEvidenceFact:
    fact_id: str
    call_range: EvidenceRange
    callee_range: EvidenceRange
    callee_kind: str
    terminal_name: str | None
    receiver_range: EvidenceRange | None
    argument_ranges: tuple[EvidenceRange, ...]
    scope_path: tuple[ScopeEvidence, ...]
    callee_path_ranges: tuple[EvidenceRange, ...]
    database_link_range: EvidenceRange | None
    grammar_rule: str
    presence: Mapping[str, Any]


@dataclass(frozen=True)
class CallableEvidenceFact:
    fact_id: str
    source_id: str
    callable_range: EvidenceRange
    name_range: EvidenceRange
    name_path_ranges: tuple[EvidenceRange, ...]
    terminal_name: str
    role: str
    ast_node_range: EvidenceRange | None
    scope_path: tuple[ScopeEvidence, ...]
    scope_range: EvidenceRange
    declaration_point: int
    syntax: CallableSyntaxEvidence
    grammar_rule: str
    presence: Mapping[str, Any]

    def as_record(self, *, code_node_id: str | None) -> dict[str, Any]:
        return {
            "fact_id": self.fact_id,
            "role": self.role,
            "code_node_id": code_node_id,
            "source_id": self.source_id,
            "range": self.callable_range.as_dict(),
            "name_range": self.name_range.as_dict(),
            "name_path_ranges": [
                item.as_dict() for item in self.name_path_ranges
            ],
            "terminal_name": self.terminal_name,
            "ast_node_range": (
                self.ast_node_range.as_dict()
                if self.ast_node_range is not None else None
            ),
            "scope_path": [item.as_record() for item in self.scope_path],
            "scope_range": self.scope_range.as_dict(),
            "declaration_point": self.declaration_point,
            "syntax": self.syntax.as_record(),
            "grammar_rule": self.grammar_rule,
            "presence_status": self.presence["status"],
            "presence": dict(self.presence),
        }


@dataclass(frozen=True)
class ParameterEvidenceFact:
    fact_id: str
    source_id: str
    parameter_range: EvidenceRange
    terminal_name: str
    scope_path: tuple[ScopeEvidence, ...]
    grammar_rule: str
    presence: Mapping[str, Any]

    def as_record(self) -> dict[str, Any]:
        return {
            "fact_id": self.fact_id,
            "source_id": self.source_id,
            "range": self.parameter_range.as_dict(),
            "terminal_name": self.terminal_name,
            "scope_path": [item.as_record() for item in self.scope_path],
            "grammar_rule": self.grammar_rule,
            "presence_status": self.presence["status"],
        }


@dataclass(frozen=True)
class BindingTargetSyntaxEvidence:
    schema: str
    root: SyntaxComponentEvidence

    def as_record(self) -> dict[str, Any]:
        return {"schema": self.schema, "root": self.root.as_record()}


@dataclass(frozen=True)
class BindingTargetEvidenceFact:
    fact_id: str
    source_id: str
    target_range: EvidenceRange
    binding_context: str
    scope_path: tuple[ScopeEvidence, ...]
    syntax: BindingTargetSyntaxEvidence
    grammar_rule: str
    presence: Mapping[str, Any]

    def as_record(self) -> dict[str, Any]:
        return {
            "fact_id": self.fact_id,
            "source_id": self.source_id,
            "range": self.target_range.as_dict(),
            "binding_context": self.binding_context,
            "scope_path": [item.as_record() for item in self.scope_path],
            "syntax": self.syntax.as_record(),
            "grammar_rule": self.grammar_rule,
            "presence_status": self.presence["status"],
        }


@dataclass(frozen=True)
class ScopeDirectiveEvidenceFact:
    fact_id: str
    source_id: str
    name_range: EvidenceRange
    terminal_name: str
    directive_kind: str
    scope_path: tuple[ScopeEvidence, ...]
    grammar_rule: str
    presence: Mapping[str, Any]

    def as_record(self) -> dict[str, Any]:
        return {
            "fact_id": self.fact_id,
            "source_id": self.source_id,
            "range": self.name_range.as_dict(),
            "terminal_name": self.terminal_name,
            "directive_kind": self.directive_kind,
            "scope_path": [item.as_record() for item in self.scope_path],
            "grammar_rule": self.grammar_rule,
            "presence_status": self.presence["status"],
        }


@dataclass(frozen=True)
class StructuralExpressionSyntaxEvidence:
    schema: str
    root: SyntaxComponentEvidence

    def as_record(self) -> dict[str, Any]:
        return {"schema": self.schema, "root": self.root.as_record()}


@dataclass(frozen=True)
class StructuralExpressionEvidenceFact:
    fact_id: str
    source_id: str
    expression_range: EvidenceRange
    owner_range: EvidenceRange
    role: str
    scope_path: tuple[ScopeEvidence, ...]
    syntax: StructuralExpressionSyntaxEvidence
    grammar_rule: str
    presence: Mapping[str, Any]

    def as_record(
        self, *, code_node_id: str, expression_source: str,
    ) -> dict[str, Any]:
        return {
            "fact_id": self.fact_id,
            "source_id": self.source_id,
            "code_node_id": code_node_id,
            "role": self.role,
            "range": self.expression_range.as_dict(),
            "owner_range": self.owner_range.as_dict(),
            "expression_source": expression_source,
            "scope_path": [item.as_record() for item in self.scope_path],
            "syntax": self.syntax.as_record(),
            "grammar_rule": self.grammar_rule,
            "presence_status": self.presence["status"],
            "presence": dict(self.presence),
        }


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
    scope_path: tuple[ScopeEvidence, ...]
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
class SymbolEvidenceFact:
    fact_id: str
    symbol_range: EvidenceRange
    role: str
    grammar_rule: str
    presence: Mapping[str, Any]
    symbol_kind: str | None = None
    scope_kind: str | None = None
    scope_range: EvidenceRange | None = None
    visibility_start_offset: int | None = None
    parser_decision: str | None = None
    resolution_status: str | None = None
    provenance: str | None = None
    definition_fact_id: str | None = None
    configured_evidence_id: str | None = None
    predicate_contexts: tuple[str, ...] = ()


@dataclass(frozen=True)
class ValidatedCommonEvidence:
    version: str
    language: str | None
    frontend_schema: str | None
    source_id: str
    decoded_text_sha256: str
    decoded_text: str
    call_facts: tuple[CallEvidenceFact, ...]
    callable_facts: tuple[CallableEvidenceFact, ...]
    parameter_facts: tuple[ParameterEvidenceFact, ...]
    binding_target_facts: tuple[BindingTargetEvidenceFact, ...]
    scope_directive_facts: tuple[ScopeDirectiveEvidenceFact, ...]
    structural_expression_facts: tuple[StructuralExpressionEvidenceFact, ...]
    import_facts: tuple[ImportEvidenceFact, ...]
    macro_facts: tuple[MacroEvidenceFact, ...]
    symbol_facts: tuple[SymbolEvidenceFact, ...]
    syntax_ambiguities: tuple[SymbolEvidenceFact, ...]
    completeness: Mapping[str, Mapping[str, Any]]
    configured_preprocessing: Mapping[str, Any] | None

    def source_record(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "source_id": self.source_id,
            "decoded_text_sha256": self.decoded_text_sha256,
            "decoded_text": self.decoded_text,
        }
        if self.language is not None:
            result["language"] = self.language
        if self.frontend_schema is not None:
            result["frontend_schema"] = self.frontend_schema
        return result

    def completeness_records(self) -> list[dict[str, Any]]:
        return [
            {"source_id": self.source_id, **dict(self.completeness[kind])}
            for kind in self.completeness
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


def _nullable_nonblank(value: Any, *, field: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise _fail(field, "null or nonblank string required")
    return value


def _nullable_evidence_id(value: Any, *, field: str) -> str | None:
    item = _nullable_nonblank(value, field=field)
    if item is not None and (
        len(item) != 64
        or any(char not in "0123456789abcdef" for char in item)
    ):
        raise _fail(field, "null or lowercase SHA-256 ID required")
    return item


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


def _contains(outer: EvidenceRange, inner: EvidenceRange) -> bool:
    return outer.char_offset <= inner.char_offset and inner.end <= outer.end


def _validate_scope_path(
    raw: Any,
    *,
    field: str,
    source_length: int,
    occurrence_range: EvidenceRange,
) -> tuple[ScopeEvidence, ...]:
    if not isinstance(raw, list) or not raw:
        raise _fail(field, "nonempty lexical scope path required")
    scopes: list[ScopeEvidence] = []
    for index, value in enumerate(raw):
        item_field = f"{field}[{index}]"
        if not isinstance(value, dict):
            raise _fail(item_field, "object required")
        _require_exact_fields(value, {"kind", "range"}, field=item_field)
        kind = value.get("kind")
        if kind not in _SCOPE_KINDS:
            raise _fail(f"{item_field}.kind", f"invalid value {kind!r}")
        scope_range = _range(
            value.get("range"), field=f"{item_field}.range",
            source_length=source_length,
        )
        if scope_range.char_length == 0:
            raise _fail(f"{item_field}.range", "nonempty scope range required")
        if scopes and not _contains(scopes[-1].range, scope_range):
            raise _fail(item_field, "scope must be contained by its parent")
        scopes.append(ScopeEvidence(kind=str(kind), range=scope_range))
    if scopes[0].kind != "translation_unit":
        raise _fail(field, "first scope must be translation_unit")
    if not _contains(scopes[-1].range, occurrence_range):
        raise _fail(field, "innermost scope must contain the occurrence")
    return tuple(scopes)


def _validate_syntax_component(
    raw: Any,
    *,
    field: str,
    source: str,
    outer: EvidenceRange,
    token_coordinates: set[tuple[int, int]],
) -> SyntaxComponentEvidence:
    if not isinstance(raw, dict):
        raise _fail(field, "object required")
    _require_exact_fields(
        raw, {"grammarRule", "range", "directTokens", "children"}, field=field,
    )
    grammar_rule = raw.get("grammarRule")
    if not isinstance(grammar_rule, str) or not grammar_rule:
        raise _fail(f"{field}.grammarRule", "nonblank string required")
    component_range = _range(
        raw.get("range"), field=f"{field}.range", source_length=len(source),
    )
    if component_range.char_length == 0 or not _contains(outer, component_range):
        raise _fail(f"{field}.range", "nonempty enclosing component range required")
    raw_tokens = raw.get("directTokens")
    raw_children = raw.get("children")
    if not isinstance(raw_tokens, list) or not isinstance(raw_children, list):
        raise _fail(field, "directTokens and children arrays required")
    tokens: list[SyntaxTokenEvidence] = []
    previous_token_end = component_range.char_offset
    for index, value in enumerate(raw_tokens):
        token_field = f"{field}.directTokens[{index}]"
        if not isinstance(value, dict):
            raise _fail(token_field, "object required")
        _require_exact_fields(value, {"tokenKind", "range"}, field=token_field)
        token_kind = value.get("tokenKind")
        if not isinstance(token_kind, str) or not token_kind:
            raise _fail(f"{token_field}.tokenKind", "nonblank string required")
        token_range = _range(
            value.get("range"), field=f"{token_field}.range",
            source_length=len(source),
        )
        coordinate = (token_range.char_offset, token_range.char_length)
        if (
            token_range.char_length == 0
            or not _contains(component_range, token_range)
            or token_range.char_offset < previous_token_end
            or coordinate in token_coordinates
        ):
            raise _fail(token_field, "exact ordered direct terminal required once")
        previous_token_end = token_range.end
        token_coordinates.add(coordinate)
        tokens.append(SyntaxTokenEvidence(
            token_kind=token_kind,
            range=token_range,
            text=source[token_range.char_offset:token_range.end],
        ))
    children: list[SyntaxComponentEvidence] = []
    previous_child_start = component_range.char_offset
    for index, value in enumerate(raw_children):
        child = _validate_syntax_component(
            value,
            field=f"{field}.children[{index}]",
            source=source,
            outer=component_range,
            token_coordinates=token_coordinates,
        )
        if child.range.char_offset < previous_child_start:
            raise _fail(f"{field}.children[{index}]", "source order required")
        previous_child_start = child.range.char_offset
        children.append(child)
    return SyntaxComponentEvidence(
        grammar_rule=grammar_rule,
        range=component_range,
        direct_tokens=tuple(tokens),
        children=tuple(children),
    )


def _validate_call(
    fact: Mapping[str, Any],
    *,
    version: str,
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
    if version == "2.0.0":
        required_fields = {
            "calleeRange", "calleeKind", "terminalName", "receiverRange",
            "argumentRanges", "scopePath",
        }
        optional_fields = {"calleePathRanges", "databaseLinkRange"}
        actual_fields = set(payload)
        if not required_fields.issubset(actual_fields) or not actual_fields.issubset(
            required_fields | optional_fields
        ):
            raise _fail(
                "call.payload",
                "required fields and optional SQL callee structure only; "
                f"missing={sorted(required_fields - actual_fields)}, "
                f"unexpected={sorted(actual_fields - required_fields - optional_fields)}",
            )
        forbidden = set(payload) & _IR2_FORBIDDEN_SEMANTIC_FIELDS
        if forbidden:
            raise _fail("call.payload", f"Parser semantic fields forbidden: {sorted(forbidden)}")
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
    if "binding" in payload:
        raise _fail(
            "call.payload.binding", "Parser final binding is forbidden",
        )
    receiver_value = payload.get("receiverRange")
    receiver_range = None if receiver_value is None else _range(
        receiver_value, field="call.payload.receiverRange", source_length=len(source),
    )
    if receiver_range is not None and (
        receiver_range.char_length == 0
        or not _contains(callee_range, receiver_range)
    ):
        raise _fail("call.payload.receiverRange", "invalid receiver subrange")
    has_path = "calleePathRanges" in payload
    has_link = "databaseLinkRange" in payload
    if has_path != has_link:
        raise _fail(
            "call.payload", "calleePathRanges and databaseLinkRange must be supplied together",
        )
    callee_path_ranges: list[EvidenceRange] = []
    database_link_range: EvidenceRange | None = None
    if has_path:
        path_values = payload.get("calleePathRanges")
        if not isinstance(path_values, list) or not path_values:
            raise _fail("call.payload.calleePathRanges", "nonempty array required")
        previous_component_end = callee_range.char_offset
        for index, value in enumerate(path_values):
            component = _range(
                value,
                field=f"call.payload.calleePathRanges[{index}]",
                source_length=len(source),
            )
            if (
                component.char_length == 0
                or not _contains(callee_range, component)
                or component.char_offset < previous_component_end
            ):
                raise _fail(
                    f"call.payload.calleePathRanges[{index}]",
                    "ordered nonempty callee subrange required",
                )
            previous_component_end = component.end
            callee_path_ranges.append(component)
        if terminal_name is not None:
            terminal_component = callee_path_ranges[-1]
            if source[terminal_component.char_offset:terminal_component.end] != terminal_name:
                raise _fail(
                    "call.payload.calleePathRanges",
                    "last path component must be the exact terminalName",
                )
        link_value = payload.get("databaseLinkRange")
        if link_value is not None:
            database_link_range = _range(
                link_value,
                field="call.payload.databaseLinkRange",
                source_length=len(source),
            )
            if (
                database_link_range.char_length == 0
                or not _contains(callee_range, database_link_range)
                or database_link_range.char_offset < previous_component_end
            ):
                raise _fail(
                    "call.payload.databaseLinkRange",
                    "nonempty callee subrange after path components required",
                )
    scope_path = (
        _validate_scope_path(
            payload.get("scopePath"), field="call.payload.scopePath",
            source_length=len(source), occurrence_range=fact_range,
        )
        if version == "2.0.0" else ()
    )
    return CallEvidenceFact(
        fact_id=str(fact.get("factId") or ""),
        call_range=fact_range,
        callee_range=callee_range,
        callee_kind=str(callee_kind),
        terminal_name=terminal_name,
        receiver_range=receiver_range,
        argument_ranges=tuple(arguments),
        scope_path=scope_path,
        callee_path_ranges=tuple(callee_path_ranges),
        database_link_range=database_link_range,
        grammar_rule=grammar_rule,
        presence=dict(presence),
    )


def _validate_callable(
    fact: Mapping[str, Any],
    *,
    source_id: str,
    fact_range: EvidenceRange,
    grammar_rule: str,
    presence: Mapping[str, Any],
    source: str,
) -> CallableEvidenceFact:
    field = "callable.payload"
    payload = fact.get("payload")
    if fact_range.char_length == 0 or not isinstance(payload, dict):
        raise _fail(field, "nonempty callable range and object payload required")
    required_fields = {
        "role", "nameRange", "astNodeRange", "scopePath", "scopeRange",
        "declarationPoint", "syntax",
    }
    optional_fields = {"namePathRanges"}
    actual_fields = set(payload)
    if not required_fields.issubset(actual_fields) or not actual_fields.issubset(
        required_fields | optional_fields
    ):
        raise _fail(
            field,
            "required callable fields and optional name path only; "
            f"missing={sorted(required_fields - actual_fields)}, "
            f"unexpected={sorted(actual_fields - required_fields - optional_fields)}",
        )
    forbidden = set(payload) & _IR2_FORBIDDEN_SEMANTIC_FIELDS
    if forbidden:
        raise _fail(field, f"Parser semantic fields forbidden: {sorted(forbidden)}")
    name_range = _range(
        payload.get("nameRange"), field=f"{field}.nameRange",
        source_length=len(source),
    )
    if (
        name_range.char_length == 0
        or name_range.char_offset < fact_range.char_offset
        or name_range.end > fact_range.end
    ):
        raise _fail(f"{field}.nameRange", "nonempty callable subrange required")
    terminal_name = source[name_range.char_offset:name_range.end]
    name_path_ranges: list[EvidenceRange] = []
    if "namePathRanges" in payload:
        raw_name_path = payload.get("namePathRanges")
        if not isinstance(raw_name_path, list) or not raw_name_path:
            raise _fail(f"{field}.namePathRanges", "nonempty array required")
        previous_end = -1
        for index, value in enumerate(raw_name_path):
            component = _range(
                value,
                field=f"{field}.namePathRanges[{index}]",
                source_length=len(source),
            )
            if component.char_length == 0 or component.char_offset < previous_end:
                raise _fail(
                    f"{field}.namePathRanges[{index}]",
                    "ordered nonempty source range required",
                )
            previous_end = component.end
            name_path_ranges.append(component)
        if name_path_ranges[-1] != name_range:
            raise _fail(
                f"{field}.namePathRanges", "last component must equal nameRange",
            )
    role = payload.get("role")
    if role not in _CALLABLE_ROLES:
        raise _fail(f"{field}.role", f"invalid value {role!r}")
    ast_node_value = payload.get("astNodeRange")
    ast_node_range = (
        None if ast_node_value is None else _range(
            ast_node_value, field=f"{field}.astNodeRange",
            source_length=len(source),
        )
    )
    if (role == "definition") != (ast_node_range is not None):
        raise _fail(field, "only definitions require astNodeRange")
    if role == "definition" and ast_node_range != fact_range:
        raise _fail(field, "definition astNodeRange must equal callable range")
    scope_path = _validate_scope_path(
        payload.get("scopePath"), field=f"{field}.scopePath",
        source_length=len(source), occurrence_range=name_range,
    )
    scope_range = _range(
        payload.get("scopeRange"), field=f"{field}.scopeRange",
        source_length=len(source),
    )
    if scope_range != scope_path[-1].range:
        raise _fail(f"{field}.scopeRange", "must equal the innermost scope range")
    declaration_point = _integer(
        payload.get("declarationPoint"), field=f"{field}.declarationPoint",
    )
    if not name_range.end <= declaration_point <= scope_range.end:
        raise _fail(f"{field}.declarationPoint", "invalid declaration boundary")
    raw_syntax = payload.get("syntax")
    if not isinstance(raw_syntax, dict):
        raise _fail(f"{field}.syntax", "object required")
    _require_exact_fields(
        raw_syntax,
        {"schema", "declarationSpecifiers", "declarator", "attributes"},
        field=f"{field}.syntax",
    )
    schema = raw_syntax.get("schema")
    if not isinstance(schema, str) or not schema:
        raise _fail(f"{field}.syntax.schema", "nonblank string required")
    raw_specifiers = raw_syntax.get("declarationSpecifiers")
    raw_attributes = raw_syntax.get("attributes")
    if not isinstance(raw_specifiers, list) or not isinstance(raw_attributes, list):
        raise _fail(f"{field}.syntax", "specifier and attribute arrays required")
    token_coordinates: set[tuple[int, int]] = set()
    specifiers = tuple(
        _validate_syntax_component(
            value, field=f"{field}.syntax.declarationSpecifiers[{index}]",
            source=source, outer=fact_range, token_coordinates=token_coordinates,
        )
        for index, value in enumerate(raw_specifiers)
    )
    declarator = _validate_syntax_component(
        raw_syntax.get("declarator"), field=f"{field}.syntax.declarator",
        source=source, outer=fact_range, token_coordinates=token_coordinates,
    )
    attributes = tuple(
        _validate_syntax_component(
            value, field=f"{field}.syntax.attributes[{index}]",
            source=source, outer=fact_range, token_coordinates=token_coordinates,
        )
        for index, value in enumerate(raw_attributes)
    )
    syntax = CallableSyntaxEvidence(
        schema=schema,
        declaration_specifiers=specifiers,
        declarator=declarator,
        attributes=attributes,
    )
    return CallableEvidenceFact(
        fact_id=str(fact.get("factId") or ""),
        source_id=source_id,
        callable_range=fact_range,
        name_range=name_range,
        name_path_ranges=tuple(name_path_ranges),
        terminal_name=terminal_name,
        role=str(role),
        ast_node_range=ast_node_range,
        scope_path=scope_path,
        scope_range=scope_range,
        declaration_point=declaration_point,
        syntax=syntax,
        grammar_rule=grammar_rule,
        presence=dict(presence),
    )


def _validate_structural_expression(
    fact: Mapping[str, Any],
    *,
    source_id: str,
    fact_range: EvidenceRange,
    grammar_rule: str,
    presence: Mapping[str, Any],
    source: str,
) -> StructuralExpressionEvidenceFact:
    field = "structural_expression.payload"
    payload = fact.get("payload")
    if fact_range.char_length == 0 or not isinstance(payload, dict):
        raise _fail(field, "nonempty expression range and object payload required")
    _require_exact_fields(
        payload, {"role", "ownerRange", "scopePath", "syntax"}, field=field,
    )
    forbidden = set(payload) & _IR2_FORBIDDEN_SEMANTIC_FIELDS
    if forbidden:
        raise _fail(field, f"Parser semantic fields forbidden: {sorted(forbidden)}")
    role = payload.get("role")
    if role not in _STRUCTURAL_EXPRESSION_ROLES:
        raise _fail(f"{field}.role", f"invalid value {role!r}")
    owner_range = _range(
        payload.get("ownerRange"), field=f"{field}.ownerRange",
        source_length=len(source),
    )
    if owner_range.char_length == 0 or not _contains(owner_range, fact_range):
        raise _fail(f"{field}.ownerRange", "must contain the expression range")
    scope_path = _validate_scope_path(
        payload.get("scopePath"), field=f"{field}.scopePath",
        source_length=len(source), occurrence_range=fact_range,
    )
    raw_syntax = payload.get("syntax")
    if not isinstance(raw_syntax, dict):
        raise _fail(f"{field}.syntax", "object required")
    _require_exact_fields(raw_syntax, {"schema", "root"}, field=f"{field}.syntax")
    schema = raw_syntax.get("schema")
    if not isinstance(schema, str) or not schema:
        raise _fail(f"{field}.syntax.schema", "nonblank string required")
    token_coordinates: set[tuple[int, int]] = set()
    root = _validate_syntax_component(
        raw_syntax.get("root"), field=f"{field}.syntax.root",
        source=source, outer=fact_range, token_coordinates=token_coordinates,
    )
    if root.range != fact_range:
        raise _fail(f"{field}.syntax.root.range", "must equal the fact range")
    if root.grammar_rule != grammar_rule:
        raise _fail(
            f"{field}.syntax.root.grammarRule",
            "must equal grammarRuleRef",
        )
    return StructuralExpressionEvidenceFact(
        fact_id=str(fact.get("factId") or ""),
        source_id=source_id,
        expression_range=fact_range,
        owner_range=owner_range,
        role=str(role),
        scope_path=scope_path,
        syntax=StructuralExpressionSyntaxEvidence(schema=schema, root=root),
        grammar_rule=grammar_rule,
        presence=dict(presence),
    )


def _validate_parameter(
    fact: Mapping[str, Any],
    *,
    source_id: str,
    fact_range: EvidenceRange,
    grammar_rule: str,
    presence: Mapping[str, Any],
    source: str,
) -> ParameterEvidenceFact:
    field = "parameter.payload"
    if fact_range.char_length == 0:
        raise _fail("parameter.range", "nonempty grammar name range required")
    payload = fact.get("payload")
    if not isinstance(payload, dict):
        raise _fail(field, "object required")
    _require_exact_fields(payload, {"scopePath"}, field=field)
    scope_path = _validate_scope_path(
        payload.get("scopePath"), field=f"{field}.scopePath",
        source_length=len(source), occurrence_range=fact_range,
    )
    owner = scope_path[-1]
    if owner.kind not in {"function", "lambda"}:
        raise _fail(
            f"{field}.scopePath",
            "innermost parameter scope must be a function or lambda",
        )
    terminal_name = source[fact_range.char_offset:fact_range.end]
    if not terminal_name:
        raise _fail("parameter.range", "name source slice is empty")
    return ParameterEvidenceFact(
        fact_id=str(fact.get("factId") or ""),
        source_id=source_id,
        parameter_range=fact_range,
        terminal_name=terminal_name,
        scope_path=scope_path,
        grammar_rule=grammar_rule,
        presence=dict(presence),
    )


def _validate_binding_target(
    fact: Mapping[str, Any],
    *,
    source_id: str,
    fact_range: EvidenceRange,
    grammar_rule: str,
    presence: Mapping[str, Any],
    source: str,
) -> BindingTargetEvidenceFact:
    field = "binding_target.payload"
    if fact_range.char_length == 0:
        raise _fail("binding_target.range", "nonempty grammar target range required")
    payload = fact.get("payload")
    if not isinstance(payload, dict):
        raise _fail(field, "object required")
    _require_exact_fields(
        payload, {"bindingContext", "scopePath", "syntax"}, field=field,
    )
    forbidden = set(payload) & _IR2_FORBIDDEN_SEMANTIC_FIELDS
    if forbidden:
        raise _fail(field, f"Parser semantic fields forbidden: {sorted(forbidden)}")
    binding_context = payload.get("bindingContext")
    if binding_context not in _BINDING_TARGET_CONTEXTS:
        raise _fail(
            f"{field}.bindingContext", f"invalid value {binding_context!r}",
        )
    scope_path = _validate_scope_path(
        payload.get("scopePath"), field=f"{field}.scopePath",
        source_length=len(source), occurrence_range=fact_range,
    )
    raw_syntax = payload.get("syntax")
    if not isinstance(raw_syntax, dict):
        raise _fail(f"{field}.syntax", "object required")
    _require_exact_fields(raw_syntax, {"schema", "root"}, field=f"{field}.syntax")
    schema = raw_syntax.get("schema")
    if schema != "python-binding-target-syntax/v1":
        raise _fail(
            f"{field}.syntax.schema",
            f"expected 'python-binding-target-syntax/v1', got {schema!r}",
        )
    root = _validate_syntax_component(
        raw_syntax.get("root"), field=f"{field}.syntax.root",
        source=source, outer=fact_range, token_coordinates=set(),
    )
    if root.range != fact_range:
        raise _fail(f"{field}.syntax.root.range", "must equal the fact range")
    if root.grammar_rule != grammar_rule:
        raise _fail(
            f"{field}.syntax.root.grammarRule", "must equal grammarRuleRef",
        )
    return BindingTargetEvidenceFact(
        fact_id=str(fact.get("factId") or ""),
        source_id=source_id,
        target_range=fact_range,
        binding_context=str(binding_context),
        scope_path=scope_path,
        syntax=BindingTargetSyntaxEvidence(schema=str(schema), root=root),
        grammar_rule=grammar_rule,
        presence=dict(presence),
    )


def _validate_scope_directive(
    fact: Mapping[str, Any],
    *,
    source_id: str,
    fact_range: EvidenceRange,
    grammar_rule: str,
    presence: Mapping[str, Any],
    source: str,
) -> ScopeDirectiveEvidenceFact:
    field = "scope_directive.payload"
    if fact_range.char_length == 0:
        raise _fail("scope_directive.range", "nonempty grammar name range required")
    payload = fact.get("payload")
    if not isinstance(payload, dict):
        raise _fail(field, "object required")
    _require_exact_fields(payload, {"directiveKind", "scopePath"}, field=field)
    forbidden = set(payload) & _IR2_FORBIDDEN_SEMANTIC_FIELDS
    if forbidden:
        raise _fail(field, f"Parser semantic fields forbidden: {sorted(forbidden)}")
    directive_kind = payload.get("directiveKind")
    if directive_kind not in _SCOPE_DIRECTIVE_KINDS:
        raise _fail(
            f"{field}.directiveKind", f"invalid value {directive_kind!r}",
        )
    scope_path = _validate_scope_path(
        payload.get("scopePath"), field=f"{field}.scopePath",
        source_length=len(source), occurrence_range=fact_range,
    )
    terminal_name = source[fact_range.char_offset:fact_range.end]
    if not terminal_name:
        raise _fail("scope_directive.range", "name source slice is empty")
    return ScopeDirectiveEvidenceFact(
        fact_id=str(fact.get("factId") or ""),
        source_id=source_id,
        name_range=fact_range,
        terminal_name=terminal_name,
        directive_kind=str(directive_kind),
        scope_path=scope_path,
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


def _optional_evidence_id(value: Any, *, field: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or len(value) != 64 or any(
        char not in "0123456789abcdef" for char in value
    ):
        raise _fail(field, "null or lowercase SHA-256 ID required")
    return value


def _validate_symbol(
    fact: Mapping[str, Any],
    *,
    fact_range: EvidenceRange,
    grammar_rule: str,
    presence: Mapping[str, Any],
    source_length: int,
) -> SymbolEvidenceFact:
    field = "symbol.payload"
    if fact_range.char_length == 0:
        raise _fail("symbol.range", "nonempty identifier range required")
    payload = fact.get("payload")
    if not isinstance(payload, dict):
        raise _fail(field, "object required")
    role = payload.get("role")
    if role == "definition":
        _require_exact_fields(payload, {
            "role", "symbolKind", "scopeKind", "scopeRange",
            "visibilityStartOffset",
        }, field=field)
        symbol_kind = payload.get("symbolKind")
        if symbol_kind not in _SYMBOL_KINDS:
            raise _fail(f"{field}.symbolKind", f"invalid {symbol_kind!r}")
        scope_kind = payload.get("scopeKind")
        if scope_kind not in _SYMBOL_SCOPES:
            raise _fail(f"{field}.scopeKind", f"invalid {scope_kind!r}")
        scope_range = _range(
            payload.get("scopeRange"), field=f"{field}.scopeRange",
            source_length=source_length,
        )
        visibility = _integer(
            payload.get("visibilityStartOffset"),
            field=f"{field}.visibilityStartOffset",
        )
        if not (
            scope_range.char_offset <= fact_range.char_offset
            and fact_range.end <= visibility <= scope_range.end
        ):
            raise _fail(
                f"{field}.visibilityStartOffset",
                "definition range <= visibility <= lexical scope end required",
            )
        return SymbolEvidenceFact(
            fact_id=str(fact.get("factId") or ""),
            symbol_range=fact_range,
            role="definition",
            grammar_rule=grammar_rule,
            presence=dict(presence),
            symbol_kind=str(symbol_kind),
            scope_kind=str(scope_kind),
            scope_range=scope_range,
            visibility_start_offset=visibility,
        )
    if role != "lookup":
        raise _fail(f"{field}.role", f"invalid {role!r}")
    _require_exact_fields(payload, {
        "role", "lookupKind", "parserDecision", "resolutionStatus",
        "provenance", "definitionFactId", "configuredEvidenceId",
        "predicateContexts",
    }, field=field)
    if payload.get("lookupKind") != "type_name":
        raise _fail(f"{field}.lookupKind", "expected 'type_name'")
    decision = payload.get("parserDecision")
    if decision not in _SYMBOL_DECISIONS:
        raise _fail(f"{field}.parserDecision", f"invalid {decision!r}")
    status = payload.get("resolutionStatus")
    if status not in {"resolved", "unresolved"}:
        raise _fail(f"{field}.resolutionStatus", f"invalid {status!r}")
    provenance = payload.get("provenance")
    if provenance not in _SYMBOL_PROVENANCE:
        raise _fail(f"{field}.provenance", f"invalid {provenance!r}")
    definition_id = _optional_evidence_id(
        payload.get("definitionFactId"), field=f"{field}.definitionFactId",
    )
    configured_id = _optional_evidence_id(
        payload.get("configuredEvidenceId"),
        field=f"{field}.configuredEvidenceId",
    )
    contexts = _string_array(
        payload.get("predicateContexts"),
        field=f"{field}.predicateContexts",
    )
    if not contexts or len(contexts) != len(set(contexts)):
        raise _fail(
            f"{field}.predicateContexts",
            "nonempty unique values required",
        )
    expected_shape = {
        "source_declaration": ("resolved", True, False),
        "configured_preprocessing": ("resolved", False, True),
        "grammar_context": ("resolved", False, False),
        "unresolved_environment": ("unresolved", False, False),
    }[str(provenance)]
    if (
        status != expected_shape[0]
        or (definition_id is not None) != expected_shape[1]
        or (configured_id is not None) != expected_shape[2]
    ):
        raise _fail(
            field,
            f"{provenance} requires status/definition/configured shape "
            f"{expected_shape!r}",
        )
    return SymbolEvidenceFact(
        fact_id=str(fact.get("factId") or ""),
        symbol_range=fact_range,
        role="lookup",
        grammar_rule=grammar_rule,
        presence=dict(presence),
        parser_decision=str(decision),
        resolution_status=str(status),
        provenance=str(provenance),
        definition_fact_id=definition_id,
        configured_evidence_id=configured_id,
        predicate_contexts=tuple(contexts),
    )


def _validate_symbol_closure(
    symbols: list[SymbolEvidenceFact],
    *,
    completeness: Mapping[str, Mapping[str, Any]],
    configured_preprocessing: Mapping[str, Any] | None,
) -> tuple[SymbolEvidenceFact, ...]:
    definitions = {
        fact.fact_id: fact for fact in symbols if fact.role == "definition"
    }
    for fact in symbols:
        if fact.definition_fact_id is None:
            continue
        definition = definitions.get(fact.definition_fact_id)
        if definition is None:
            raise _fail(
                "symbol.payload.definitionFactId",
                f"missing definition {fact.definition_fact_id!r}",
            )
        assert definition.scope_range is not None
        assert definition.visibility_start_offset is not None
        if not (
            definition.visibility_start_offset <= fact.symbol_range.char_offset
            and definition.scope_range.char_offset <= fact.symbol_range.char_offset
            and fact.symbol_range.end <= definition.scope_range.end
        ):
            raise _fail(
                "symbol.payload.definitionFactId",
                "lookup must follow its definition inside the same lexical scope",
            )
        expected_decision = (
            "type_name"
            if definition.symbol_kind == "typedef_name"
            else "ordinary_identifier"
        )
        if fact.parser_decision != expected_decision:
            raise _fail(
                "symbol.payload.parserDecision",
                "source declaration kind and lookup decision differ",
            )
    for fact in symbols:
        if fact.provenance == "grammar_context" and (
            fact.parser_decision != "ordinary_identifier"
        ):
            raise _fail(
                "symbol.payload.parserDecision",
                "grammar context can only close an ordinary declarator decision",
            )
        if fact.configured_evidence_id is None:
            continue
        if configured_preprocessing is None:
            raise _fail(
                "symbol.payload.configuredEvidenceId",
                "configured preprocessing closure is absent",
            )
        trace = configured_preprocessing["trace"]
        if (
            configured_preprocessing["build"]["status"] != "exact"
            or configured_preprocessing["trust"] != "authoritative"
            or fact.configured_evidence_id not in trace["emittedEvidenceIds"]
        ):
            raise _fail(
                "symbol.payload.configuredEvidenceId",
                "authoritative emitted preprocessing trace evidence required",
            )
    unresolved_ids = [
        fact.fact_id for fact in symbols
        if fact.role == "lookup" and fact.resolution_status == "unresolved"
    ]
    symbol_completeness = completeness["symbol"]
    if unresolved_ids and "unresolvedFactIds" not in symbol_completeness:
        raise _fail(
            "completeness[symbol].unresolvedFactIds", "array required",
        )
    declared_ids = symbol_completeness.get("unresolvedFactIds", [])
    if declared_ids != unresolved_ids:
        raise _fail(
            "completeness[symbol].unresolvedFactIds",
            "must exactly equal unresolved symbol lookup facts in source order",
        )
    if unresolved_ids and (
        symbol_completeness.get("status") != "partial"
        or symbol_completeness.get("reason")
            != "insufficient_type_name_environment"
    ):
        raise _fail(
            "completeness[symbol]",
            "unresolved lookup facts require partial insufficient-type context",
        )
    return tuple(
        fact for fact in symbols
        if fact.role == "lookup"
        and fact.resolution_status == "unresolved"
        and (
            fact.parser_decision == "ordinary_identifier"
            or any(
                context.startswith("ambiguous_")
                for context in fact.predicate_contexts
            )
        )
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
    elif version == "2.0.0":
        _require_exact_fields(payload, {
            "directiveKind", "scopePath", "entries",
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
    scope_path = ()
    if version == "2.0.0":
        scope_path = _validate_scope_path(
            payload.get("scopePath"), field="import.payload.scopePath",
            source_length=len(source), occurrence_range=fact_range,
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
        scope_path=scope_path,
        entries=entries,
        grammar_rule=grammar_rule,
        presence=dict(presence),
    )


def _validate_completeness(
    evidence: Mapping[str, Any],
    *,
    version: str,
    emitted_by_kind: Mapping[str, int],
    fact_kinds: Mapping[str, str],
) -> dict[str, dict[str, Any]]:
    rows = evidence.get("completeness")
    if not isinstance(rows, list):
        raise _fail("completeness", "array required")
    expected_kinds = (
        _V2_COMPLETENESS_KINDS if version == "2.0.0" else _LEGACY_KINDS
    )
    allowed_kinds = expected_kinds + (
        _V2_OPTIONAL_COMPLETENESS_KINDS if version == "2.0.0" else ()
    )
    result: dict[str, dict[str, Any]] = {}
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise _fail(f"completeness[{index}]", "object required")
        kind = row.get("kind")
        if kind not in allowed_kinds or kind in result:
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
        unresolved_facts = row.get("unresolvedFactIds", [])
        expected_fact_kind = "call" if kind == "call_binding" else kind
        if (
            not isinstance(unresolved_facts, list)
            or len(unresolved_facts) != len(set(unresolved_facts))
            or not all(
                isinstance(item, str)
                and fact_kinds.get(item) == expected_fact_kind
                for item in unresolved_facts
            )
        ):
            raise _fail(
                f"completeness[{kind}].unresolvedFactIds",
                f"every ID must reference a {expected_fact_kind!r} fact in this envelope",
            )
        result[str(kind)] = dict(row)
    missing = [kind for kind in expected_kinds if kind not in result]
    missing.extend(
        kind for kind in _V2_OPTIONAL_COMPLETENESS_KINDS
        if emitted_by_kind.get(kind, 0) and kind not in result
    )
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
    language: str | None = None
    frontend_schema: str | None = None
    if version == "2.0.0":
        language_value = evidence.get("language")
        frontend_value = evidence.get("frontendSchema")
        if not isinstance(language_value, str) or not language_value:
            raise _fail("language", "IR 2.0 requires a nonblank language identity")
        if not isinstance(frontend_value, str) or not frontend_value:
            raise _fail(
                "frontendSchema", "IR 2.0 requires a nonblank frontend identity",
            )
        language = language_value
        frontend_schema = frontend_value
    configured_preprocessing = _validate_configured_preprocessing(
        evidence, source_id=source_id,
    )
    grammar_rules, presences = _validate_tables(evidence)
    facts = evidence.get("facts")
    if not isinstance(facts, list):
        raise _fail("facts", "array required")

    ordinals: dict[tuple[str, int, int], int] = {}
    fact_kinds: dict[str, str] = {}
    allowed_kinds = _FACT_KINDS if version == "2.0.0" else _LEGACY_KINDS
    emitted_by_kind = {kind: 0 for kind in allowed_kinds}
    calls: list[CallEvidenceFact] = []
    callables: list[CallableEvidenceFact] = []
    parameters: list[ParameterEvidenceFact] = []
    binding_targets: list[BindingTargetEvidenceFact] = []
    scope_directives: list[ScopeDirectiveEvidenceFact] = []
    structural_expressions: list[StructuralExpressionEvidenceFact] = []
    imports: list[ImportEvidenceFact] = []
    macros: list[MacroEvidenceFact] = []
    symbols: list[SymbolEvidenceFact] = []
    for index, fact in enumerate(facts):
        if not isinstance(fact, dict):
            raise _fail(f"facts[{index}]", "object required")
        kind = fact.get("kind")
        if kind not in allowed_kinds:
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
                version=version,
                fact_range=fact_range,
                grammar_rule=grammar_rule,
                presence=presence,
                source=source,
            ))
        elif kind == "callable":
            if version != "2.0.0":
                raise _fail(
                    f"facts[{index}].kind",
                    "callable structural payload requires common evidence 2.0.0",
                )
            callables.append(_validate_callable(
                fact,
                source_id=source_id,
                fact_range=fact_range,
                grammar_rule=grammar_rule,
                presence=presence,
                source=source,
            ))
        elif kind == "structural_expression":
            if version != "2.0.0":
                raise _fail(
                    f"facts[{index}].kind",
                    "structural expression payload requires common evidence 2.0.0",
                )
            structural_expressions.append(_validate_structural_expression(
                fact,
                source_id=source_id,
                fact_range=fact_range,
                grammar_rule=grammar_rule,
                presence=presence,
                source=source,
            ))
        elif kind == "parameter":
            if version != "2.0.0":
                raise _fail(
                    f"facts[{index}].kind",
                    "parameter structural payload requires common evidence 2.0.0",
                )
            parameters.append(_validate_parameter(
                fact,
                source_id=source_id,
                fact_range=fact_range,
                grammar_rule=grammar_rule,
                presence=presence,
                source=source,
            ))
        elif kind == "binding_target":
            if version != "2.0.0":
                raise _fail(
                    f"facts[{index}].kind",
                    "binding target structural payload requires common evidence 2.0.0",
                )
            binding_targets.append(_validate_binding_target(
                fact,
                source_id=source_id,
                fact_range=fact_range,
                grammar_rule=grammar_rule,
                presence=presence,
                source=source,
            ))
        elif kind == "scope_directive":
            if version != "2.0.0":
                raise _fail(
                    f"facts[{index}].kind",
                    "scope directive structural payload requires common evidence 2.0.0",
                )
            scope_directives.append(_validate_scope_directive(
                fact,
                source_id=source_id,
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
        elif kind == "symbol":
            if version not in {"1.2.0", "2.0.0"}:
                raise _fail(
                    f"facts[{index}].kind",
                    "symbol payload requires common evidence 1.2.0",
                )
            symbols.append(_validate_symbol(
                fact,
                fact_range=fact_range,
                grammar_rule=grammar_rule,
                presence=presence,
                source_length=len(source),
            ))

    completeness = _validate_completeness(
        evidence,
        version=version,
        emitted_by_kind=emitted_by_kind,
        fact_kinds=fact_kinds,
    )
    syntax_ambiguities = _validate_symbol_closure(
        symbols,
        completeness=completeness,
        configured_preprocessing=configured_preprocessing,
    ) if version in {"1.2.0", "2.0.0"} else ()
    return ValidatedCommonEvidence(
        version=version,
        language=language,
        frontend_schema=frontend_schema,
        source_id=source_id,
        decoded_text_sha256=decoded_hash,
        decoded_text=source,
        call_facts=tuple(calls),
        callable_facts=tuple(callables),
        parameter_facts=tuple(parameters),
        binding_target_facts=tuple(binding_targets),
        scope_directive_facts=tuple(scope_directives),
        structural_expression_facts=tuple(structural_expressions),
        import_facts=tuple(imports),
        macro_facts=tuple(macros),
        symbol_facts=tuple(symbols),
        syntax_ambiguities=tuple(syntax_ambiguities),
        completeness=completeness,
        configured_preprocessing=configured_preprocessing,
    )
