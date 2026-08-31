from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

import pytest

from pipeline.steps.step2_load_ast.evidence_ir import (
    CommonEvidenceContractError,
    validate_common_evidence,
)
from pipeline.steps.step2_load_ast.dispatch import read_antlr_json
from pipeline.steps.step2_load_ast.framework_reader import _bind_callable_evidence
from pipeline.steps.step2_load_ast.node_builders import ParseResult
from graph.schema.graph_nodes import CodeNode


KINDS = (
    "call", "callable", "import", "symbol", "literal", "assignment",
    "parameter", "binding_target", "scope_directive", "structural_expression", "macro", "embedded_language",
    "conditional_region",
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
    return_owner_range = _range(source, "return target(1);")
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
        {
            "factId": _fact_id(
                source_id, digest, "structural_expression", call_range,
            ),
            "kind": "structural_expression",
            "range": call_range,
            "grammarRuleRef": 2,
            "presenceRef": 0,
            "payload": {
                "role": "return_value",
                "ownerRange": return_owner_range,
                "scopePath": [{"kind": "translation_unit", "range": unit_range}],
                "syntax": {
                    "schema": "c-expression-syntax/v1",
                    "root": {
                        "grammarRule": "expression",
                        "range": call_range,
                        "directTokens": [],
                        "children": [{
                            "grammarRule": "postfixExpression",
                            "range": call_range,
                            "directTokens": [
                                {
                                    "tokenKind": "LeftParen",
                                    "range": {
                                        "charOffset": call_range["charOffset"] + 6,
                                        "charLength": 1,
                                    },
                                },
                                {
                                    "tokenKind": "RightParen",
                                    "range": {
                                        "charOffset": call_range["charOffset"] + 8,
                                        "charLength": 1,
                                    },
                                },
                            ],
                            "children": [
                                {
                                    "grammarRule": "primaryExpression",
                                    "range": call_name_range,
                                    "directTokens": [{
                                        "tokenKind": "Identifier",
                                        "range": call_name_range,
                                    }],
                                    "children": [],
                                },
                                {
                                    "grammarRule": "primaryExpression",
                                    "range": _range(source, "1"),
                                    "directTokens": [{
                                        "tokenKind": "Constant",
                                        "range": _range(source, "1"),
                                    }],
                                    "children": [],
                                },
                            ],
                        }],
                    },
                },
            },
        },
    ]
    completeness = []
    for kind in KINDS:
        emitted = 1 if kind in {
            "call", "callable", "structural_expression",
        } else 0
        completeness.append({
            "kind": kind,
            "status": "complete" if kind in {
                "call", "callable", "import", "symbol", "macro",
                "structural_expression", "conditional_region",
            } else "unsupported",
            "population": emitted,
            "emitted": emitted,
            "explicitlyUnresolved": 0,
        })
    return source, {
        "type": "FILE",
        "fileName": "slice.c",
        "filePath": source_id,
        "children": [{
            "type": "FUNCTION",
            "name": "caller",
            "signature": "int caller(void)",
            "startLine": 2,
            "endLine": 2,
            "children": [
                {
                    "type": "RETURN",
                    "startLine": 2,
                    "endLine": 2,
                    "expression": "target(1)",
                    "children": [],
                },
                {
                    "type": "FUNCTION_CALL",
                    "name": "target",
                    "startLine": 2,
                    "endLine": 2,
                    "children": [],
                },
            ],
        }],
        "evidence": {
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
        "grammarRules": ["declaration", "postfixExpression", "expression"],
        "presences": [{
            "status": "active", "condition": None,
            "configurationId": None, "provenance": "unconditional",
        }],
        "completeness": completeness,
        },
    }


def _validate(raw: dict, source: str):
    return validate_common_evidence(
        raw, source_bytes=source.encode(), expected_source_id="slice.c",
    )


def test_ir2_decodes_structural_evidence_without_parser_semantics() -> None:
    source, raw = _artifact()
    evidence = _validate(raw, source)

    assert (evidence.language, evidence.frontend_schema) == ("c", "antlr-c/v1")
    assert len(evidence.call_facts) == len(evidence.callable_facts) == 1
    assert len(evidence.structural_expression_facts) == 1
    assert evidence.call_facts[0].scope_path[0].kind == "translation_unit"
    callable_fact = evidence.callable_facts[0]
    assert callable_fact.terminal_name == "target"
    assert callable_fact.syntax.declaration_specifiers[0].direct_tokens[0].text == "static"
    record = callable_fact.as_record(code_node_id=None)
    assert "binding" not in record and "binding_key" not in record
    expression = evidence.structural_expression_facts[0]
    assert expression.role == "return_value"
    assert expression.syntax.root.children[0].direct_tokens[0].text == "("


def test_ir21_decodes_exact_grammar_owned_call_argument_structure() -> None:
    source, raw = _artifact()
    raw["evidence"]["version"] = "2.1.0"
    call_payload = next(
        fact["payload"] for fact in raw["evidence"]["facts"]
        if fact["kind"] == "call"
    )
    argument_range = call_payload.pop("argumentRanges")[0]
    call_payload["arguments"] = [{
        "range": argument_range,
        "syntaxKind": "string_literal",
        "literalKind": "string",
        "literalValue": "worker",
        "identifier": None,
    }]

    evidence = _validate(raw, source)

    argument = evidence.call_facts[0].arguments[0]
    assert argument.range.as_dict() == argument_range
    assert argument.syntax_kind == "string_literal"
    assert argument.literal_kind == "string"
    assert argument.literal_value == "worker"
    assert argument.identifier is None


def test_ir20_argument_ranges_cannot_hide_unversioned_structure() -> None:
    source, raw = _artifact()
    call_payload = next(
        fact["payload"] for fact in raw["evidence"]["facts"]
        if fact["kind"] == "call"
    )
    call_payload["argumentRanges"][0]["syntaxKind"] = "string_literal"

    with pytest.raises(CommonEvidenceContractError, match="exact fields required"):
        _validate(raw, source)


@pytest.mark.parametrize(
    "mutate",
    (
        lambda argument: argument.update(syntaxKind="identifier"),
        lambda argument: argument.update(unexpected=True),
        lambda argument: argument.pop("range"),
    ),
)
def test_ir21_rejects_mismatched_or_open_call_argument_structure(mutate) -> None:
    source, raw = _artifact()
    raw["evidence"]["version"] = "2.1.0"
    call_payload = next(
        fact["payload"] for fact in raw["evidence"]["facts"]
        if fact["kind"] == "call"
    )
    argument_range = call_payload.pop("argumentRanges")[0]
    argument = {
        "range": argument_range,
        "syntaxKind": "string_literal",
        "literalKind": "string",
        "literalValue": "worker",
        "identifier": None,
    }
    call_payload["arguments"] = [argument]
    mutate(argument)

    with pytest.raises(CommonEvidenceContractError, match="call.payload.arguments"):
        _validate(raw, source)


def test_ir2_accepts_language_class_scope_from_java_and_python_frontends() -> None:
    source, raw = _artifact()
    evidence = raw["evidence"]
    evidence["language"] = "java"
    evidence["frontendSchema"] = "antlr-java/v1"
    unit_range = {"charOffset": 0, "charLength": len(source)}
    class_scope = {"kind": "class", "range": unit_range}
    for fact in evidence["facts"]:
        if fact["kind"] in {"call", "callable"}:
            fact["payload"]["scopePath"].append(copy.deepcopy(class_scope))

    validated = _validate(raw, source)

    assert validated.language == "java"
    assert validated.frontend_schema == "antlr-java/v1"
    assert validated.call_facts[0].scope_path[-1].kind == "class"
    assert validated.callable_facts[0].scope_path[-1].kind == "class"


def test_ir2_preserves_oracle_callable_name_path_and_package_scope() -> None:
    source, raw = _artifact()
    evidence = raw["evidence"]
    evidence["language"] = "oracle"
    evidence["frontendSchema"] = "antlr-oracle/v1"
    unit_range = {"charOffset": 0, "charLength": len(source)}
    callable_payload = next(
        fact["payload"] for fact in evidence["facts"]
        if fact["kind"] == "callable"
    )
    callable_payload["namePathRanges"] = [copy.deepcopy(
        callable_payload["nameRange"],
    )]
    for fact in evidence["facts"]:
        if fact["kind"] in {"call", "callable"}:
            fact["payload"]["scopePath"].append({
                "kind": "package", "range": unit_range,
            })

    validated = _validate(raw, source)

    callable_fact = validated.callable_facts[0]
    assert callable_fact.name_path_ranges == (callable_fact.name_range,)
    assert callable_fact.scope_path[-1].kind == "package"


def test_ir2_rejects_oracle_name_path_that_does_not_end_at_callable_name() -> None:
    source, raw = _artifact()
    callable_payload = next(
        fact["payload"] for fact in raw["evidence"]["facts"]
        if fact["kind"] == "callable"
    )
    callable_payload["namePathRanges"] = [_range(source, "static")]

    with pytest.raises(
        CommonEvidenceContractError,
        match="last component must equal nameRange",
    ):
        _validate(raw, source)


def test_ir2_projects_parameter_name_and_lexical_scope_without_semantics() -> None:
    source, raw = _artifact()
    evidence = raw["evidence"]
    parameter_range = _range(source, "value")
    unit_range = {"charOffset": 0, "charLength": len(source)}
    evidence["grammarRules"].append("named_parameter")
    evidence["facts"].append({
        "factId": _fact_id(
            evidence["sourceId"], evidence["decodedTextSha256"],
            "parameter", parameter_range,
        ),
        "kind": "parameter",
        "range": parameter_range,
        "grammarRuleRef": len(evidence["grammarRules"]) - 1,
        "presenceRef": 0,
        "payload": {
            "scopePath": [
                {"kind": "translation_unit", "range": unit_range},
                {"kind": "function", "range": unit_range},
            ],
        },
    })
    parameter_completeness = next(
        row for row in evidence["completeness"] if row["kind"] == "parameter"
    )
    parameter_completeness.update({
        "status": "complete", "population": 1, "emitted": 1,
    })

    validated = _validate(raw, source)

    assert len(validated.parameter_facts) == 1
    assert validated.parameter_facts[0].terminal_name == "value"
    assert validated.parameter_facts[0].scope_path[-1].kind == "function"


def test_ir2_preserves_python_binding_target_grammar_without_final_semantics() -> None:
    source, raw = _artifact()
    evidence = raw["evidence"]
    evidence["language"] = "python"
    evidence["frontendSchema"] = "antlr-python/v1"
    target_range = _range(source, "value")
    unit_range = {"charOffset": 0, "charLength": len(source)}
    evidence["grammarRules"].append("name")
    evidence["facts"].append({
        "factId": _fact_id(
            evidence["sourceId"], evidence["decodedTextSha256"],
            "binding_target", target_range,
        ),
        "kind": "binding_target",
        "range": target_range,
        "grammarRuleRef": len(evidence["grammarRules"]) - 1,
        "presenceRef": 0,
        "payload": {
            "bindingContext": "assignment",
            "scopePath": [
                {"kind": "translation_unit", "range": unit_range},
                {"kind": "function", "range": unit_range},
            ],
            "syntax": {
                "schema": "python-binding-target-syntax/v1",
                "root": {
                    "grammarRule": "name",
                    "range": target_range,
                    "directTokens": [{"tokenKind": "NAME", "range": target_range}],
                    "children": [],
                },
            },
        },
    })
    target_completeness = next(
        row for row in evidence["completeness"] if row["kind"] == "binding_target"
    )
    target_completeness.update({
        "status": "complete", "population": 1, "emitted": 1,
    })

    validated = _validate(raw, source)

    assert len(validated.binding_target_facts) == 1
    target = validated.binding_target_facts[0]
    assert target.binding_context == "assignment"
    assert target.syntax.root.direct_tokens[0].text == "value"
    assert target.scope_path[-1].kind == "function"


def test_ir2_preserves_python_scope_directive_name_without_resolving_it() -> None:
    source, raw = _artifact()
    evidence = raw["evidence"]
    evidence["language"] = "python"
    evidence["frontendSchema"] = "antlr-python/v1"
    name_range = _range(source, "value")
    unit_range = {"charOffset": 0, "charLength": len(source)}
    evidence["grammarRules"].append("global_stmt")
    evidence["facts"].append({
        "factId": _fact_id(
            evidence["sourceId"], evidence["decodedTextSha256"],
            "scope_directive", name_range,
        ),
        "kind": "scope_directive",
        "range": name_range,
        "grammarRuleRef": len(evidence["grammarRules"]) - 1,
        "presenceRef": 0,
        "payload": {
            "directiveKind": "global",
            "scopePath": [
                {"kind": "translation_unit", "range": unit_range},
                {"kind": "function", "range": unit_range},
            ],
        },
    })
    directive_completeness = next(
        row for row in evidence["completeness"] if row["kind"] == "scope_directive"
    )
    directive_completeness.update({
        "status": "complete", "population": 1, "emitted": 1,
    })

    validated = _validate(raw, source)

    assert len(validated.scope_directive_facts) == 1
    directive = validated.scope_directive_facts[0]
    assert directive.terminal_name == "value"
    assert directive.directive_kind == "global"
    assert directive.scope_path[-1].kind == "function"


def test_ir2_import_requires_and_preserves_lexical_scope() -> None:
    source, raw = _artifact()
    evidence = raw["evidence"]
    evidence["language"] = "python"
    evidence["frontendSchema"] = "antlr-python/v1"
    target_range = _range(source, "target")
    unit_range = {"charOffset": 0, "charLength": len(source)}
    evidence["facts"].append({
        "factId": _fact_id(
            evidence["sourceId"], evidence["decodedTextSha256"],
            "import", target_range,
        ),
        "kind": "import",
        "range": target_range,
        "grammarRuleRef": 0,
        "presenceRef": 0,
        "payload": {
            "directiveKind": "import",
            "scopePath": [{"kind": "translation_unit", "range": unit_range}],
            "entries": [{
                "importKind": "module",
                "targetKind": "qualified",
                "targetRange": target_range,
                "pathComponentRanges": [target_range],
                "memberRange": None,
                "aliasRange": None,
                "relativeLevel": 0,
                "wildcard": False,
                "locality": "unspecified",
            }],
        },
    })
    import_completeness = next(
        row for row in evidence["completeness"] if row["kind"] == "import"
    )
    import_completeness.update({
        "status": "complete", "population": 1, "emitted": 1,
    })

    validated = _validate(raw, source)

    assert validated.import_facts[0].scope_path[0].kind == "translation_unit"

    del evidence["facts"][-1]["payload"]["scopePath"]
    with pytest.raises(CommonEvidenceContractError, match="import.payload"):
        _validate(raw, source)


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


def test_same_line_overloads_bind_by_single_parser_occurrence_order() -> None:
    source = "class Sample { Sample() {} Sample(int value) {} }\n"
    nodes = [
        CodeNode(
            id=f"code::Sample:Sample@1#{index}", name="Sample",
            signature=signature, file_path="Sample.java", start_line=1,
            end_line=1, label="METHOD", owner_id="code::Sample",
        )
        for index, signature in enumerate(("Sample()", "Sample(int value)"))
    ]
    facts = [
        SimpleNamespace(
                fact_id=f"constructor-{index}", role="definition",
                terminal_name="Sample",
                callable_range=SimpleNamespace(
                    char_offset=source.index(signature),
                    char_length=len(signature),
                ),
                name_path_ranges=(),
                as_record=(
                lambda *, code_node_id, fact_id=f"constructor-{index}": {
                    "fact_id": fact_id, "code_node_id": code_node_id,
                }
            ),
        )
        for index, signature in enumerate(("Sample()", "Sample(int value)"))
    ]
    result = ParseResult(code_nodes=nodes)
    evidence = SimpleNamespace(
        decoded_text=source, language="java", source_id="Sample.java",
        callable_facts=facts,
    )

    _bind_callable_evidence(result, evidence)

    assert [fact["code_node_id"] for fact in result.callable_facts] == [
        node.id for node in nodes
    ]


def test_compact_constructor_fact_binds_to_record_method_node() -> None:
    source = "record Value(int number) { Value {} }\n"
    start = source.index("Value {}")
    node = CodeNode(
        id="code::Value:Value@1#0", name="Value",
        signature="Value", file_path="Value.java", start_line=1,
        end_line=1, label="METHOD", owner_id="code::Value",
    )
    fact = SimpleNamespace(
        fact_id="compact-constructor", role="definition",
        terminal_name="Value",
        callable_range=SimpleNamespace(
            char_offset=start, char_length=len("Value {}"),
        ),
        name_path_ranges=(),
        as_record=lambda *, code_node_id: {
            "fact_id": "compact-constructor", "code_node_id": code_node_id,
        },
    )
    result = ParseResult(code_nodes=[node])
    evidence = SimpleNamespace(
        decoded_text=source, language="java", source_id="Value.java",
        callable_facts=[fact],
    )

    _bind_callable_evidence(result, evidence)

    assert result.callable_facts == [{
        "fact_id": "compact-constructor", "code_node_id": node.id,
        "name_path_components": [],
    }]


def test_callable_definitions_share_enclosing_scope_without_owner_collision() -> None:
    source = "int first(void) {}\nint second(void) {}\n"
    unit_range = SimpleNamespace(char_offset=0, char_length=len(source))
    definitions = (("first", "int first(void) {}"), ("second", "int second(void) {}"))
    nodes = [
        CodeNode(
            id=f"code::{name}", name=name, signature=text,
            file_path="slice.c", start_line=index, end_line=index,
            label="FUNCTION", owner_id="code::slice.c",
        )
        for index, (name, text) in enumerate(definitions, start=1)
    ]
    facts = []
    for name, text in definitions:
        start = source.index(text)
        facts.append(SimpleNamespace(
            fact_id=f"callable-{name}", role="definition", terminal_name=name,
            callable_range=SimpleNamespace(
                char_offset=start, char_length=len(text),
            ),
            scope_range=unit_range,
            name_path_ranges=(),
            as_record=lambda *, code_node_id, name=name: {
                "fact_id": f"callable-{name}", "code_node_id": code_node_id,
            },
        ))
    result = ParseResult(code_nodes=nodes)
    evidence = SimpleNamespace(
        decoded_text=source, language="c", source_id="slice.c",
        callable_facts=facts,
    )

    _bind_callable_evidence(result, evidence)

    assert [record["code_node_id"] for record in result.callable_facts] == [
        "code::first", "code::second",
    ]


def test_ir2_binds_structural_expression_to_exact_legacy_field_once() -> None:
    source, raw = _artifact()
    with TemporaryDirectory() as directory:
        root = Path(directory)
        (root / "slice.c").write_text(source, encoding="utf-8", newline="")
        artifact = root / "slice.json"
        artifact.write_text(json.dumps(raw), encoding="utf-8")

        result = read_antlr_json(artifact, root, source_language="c")

    assert len(result.structural_expression_facts) == 1
    fact = result.structural_expression_facts[0]
    return_node = next(node for node in result.code_nodes if node.label == "RETURN")
    assert fact["code_node_id"] == return_node.id
    assert fact["role"] == "return_value"
    assert fact["expression_source"] == "target(1)"
    assert [item["fact_id"] for item in result.semantic_expression_facts] == [
        fact["fact_id"],
    ]
    assert result.semantic_expression_facts[0]["root"]["form"] == "root"
    assert [item["fact_id"] for item in result.resolved_expression_facts] == [
        fact["fact_id"],
    ]
    assert result.resolved_expression_facts[0]["expression"]["kind"] == "call"


def test_ir13_parser_semantic_wire_is_not_a_supported_fallback() -> None:
    source, raw = _artifact()
    raw["evidence"]["version"] = "1.3.0"

    with pytest.raises(CommonEvidenceContractError, match="supported versions"):
        _validate(raw, source)
