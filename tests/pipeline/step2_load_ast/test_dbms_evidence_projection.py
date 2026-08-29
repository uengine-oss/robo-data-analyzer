from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from pipeline.steps.step2_load_ast.dispatch import read_antlr_json
from pipeline.steps.step5_linking.link_calls.call_classifier import classify_call_sites
from pipeline.steps.step5_linking.link_calls.link_parser_calls_step import (
    link_parser_calls_step,
)
from pipeline.strategies import DBMS_PIPELINE


_KINDS = (
    "call", "callable", "import", "symbol", "literal", "assignment",
    "parameter", "binding_target", "scope_directive", "structural_expression",
    "macro", "embedded_language", "conditional_region",
)


def _range(source: str, value: str, *, start: int = 0) -> dict[str, int]:
    offset = source.index(value, start)
    return {"charOffset": offset, "charLength": len(value)}


def _fact_id(
    source_id: str,
    digest: str,
    kind: str,
    value: dict[str, int],
) -> str:
    encoded = "\0".join((
        "robo-evidence-v1",
        source_id,
        digest,
        kind,
        f"{value['charOffset']}:{value['charLength']}",
        "0",
    ))
    return hashlib.sha256(encoded.encode()).hexdigest()


def _component(
    grammar_rule: str,
    value_range: dict[str, int],
    *,
    token_kind: str | None = None,
) -> dict:
    return {
        "grammarRule": grammar_rule,
        "range": value_range,
        "directTokens": ([{
            "tokenKind": token_kind,
            "range": value_range,
        }] if token_kind else []),
        "children": [],
    }


def _artifact() -> tuple[str, dict]:
    source = (
        "CREATE OR REPLACE FUNCTION app.run() RETURNS void LANGUAGE plpgsql AS $$\n"
        "BEGIN\n"
        "  PERFORM app.target();\n"
        "END;\n"
        "$$;\n"
        "CREATE OR REPLACE FUNCTION app.target() RETURNS void LANGUAGE plpgsql AS $$\n"
        "BEGIN\n"
        "  NULL;\n"
        "END;\n"
        "$$;\n"
    )
    source_id = "sample.sql"
    digest = hashlib.sha256(source.encode()).hexdigest()
    unit_range = {"charOffset": 0, "charLength": len(source)}
    second_start = source.index("CREATE OR REPLACE FUNCTION", 1)
    run_range = {
        "charOffset": 0,
        "charLength": second_start - 1,
    }
    target_range = {
        "charOffset": second_start,
        "charLength": len(source) - second_start - 1,
    }
    run_name = _range(source, "run")
    run_schema = _range(source, "app")
    target_name = _range(source, "target", start=second_start)
    target_schema = _range(source, "app", start=second_start)
    run_declarator = _range(source, "app.run()")
    target_declarator = _range(source, "app.target()", start=second_start)
    function_keyword = _range(source, "FUNCTION")
    target_function_keyword = _range(source, "FUNCTION", start=second_start)

    def callable_fact(
        fact_range: dict[str, int],
        schema_range: dict[str, int],
        name_range: dict[str, int],
        declarator_range: dict[str, int],
        keyword_range: dict[str, int],
    ) -> dict:
        return {
            "factId": _fact_id(source_id, digest, "callable", fact_range),
            "kind": "callable",
            "range": fact_range,
            "grammarRuleRef": 0,
            "presenceRef": 0,
            "payload": {
                "role": "definition",
                "nameRange": name_range,
                "namePathRanges": [schema_range, name_range],
                "astNodeRange": fact_range,
                "scopePath": [
                    {"kind": "translation_unit", "range": unit_range},
                    {"kind": "function", "range": fact_range},
                ],
                "scopeRange": fact_range,
                "declarationPoint": (
                    declarator_range["charOffset"] + declarator_range["charLength"]
                ),
                "syntax": {
                    "schema": "postgresql-callable-syntax/v1",
                    "declarationSpecifiers": [
                        _component(
                            "postgresql_callable_kind",
                            keyword_range,
                            token_kind="FUNCTION",
                        ),
                    ],
                    "declarator": _component(
                        "postgresql_callable_declarator", declarator_range,
                    ),
                    "attributes": [],
                },
            },
        }

    call_range = _range(source, "app.target()")
    call_callee = _range(source, "app.target")
    call_schema = _range(source, "app", start=call_range["charOffset"])
    call_name = _range(source, "target", start=call_range["charOffset"])
    call_fact = {
        "factId": _fact_id(source_id, digest, "call", call_range),
        "kind": "call",
        "range": call_range,
        "grammarRuleRef": 1,
        "presenceRef": 0,
        "payload": {
            "calleeRange": call_callee,
            "receiverRange": call_schema,
            "calleeKind": "named",
            "terminalName": "target",
            "argumentRanges": [],
            "scopePath": [
                {"kind": "translation_unit", "range": unit_range},
                {"kind": "function", "range": run_range},
                {"kind": "block", "range": _range(source, "BEGIN\n  PERFORM app.target();\nEND;")},
            ],
            "calleePathRanges": [call_schema, call_name],
            "databaseLinkRange": None,
        },
    }
    facts = [
        callable_fact(
            run_range, run_schema, run_name, run_declarator, function_keyword,
        ),
        callable_fact(
            target_range, target_schema, target_name,
            target_declarator, target_function_keyword,
        ),
        call_fact,
    ]
    completeness = [
        {
            "kind": kind,
            "status": (
                "complete" if kind in {
                    "call", "callable", "import", "symbol", "macro",
                    "conditional_region",
                } else "unsupported"
            ),
            "population": 1 if kind == "call" else 2 if kind == "callable" else 0,
            "emitted": 1 if kind == "call" else 2 if kind == "callable" else 0,
            "explicitlyUnresolved": 0,
        }
        for kind in _KINDS
    ]
    return source, {
        "type": "FILE",
        "fileName": source_id,
        "filePath": source_id,
        "children": [
            {
                "type": "PROCEDURE",
                "name": "run",
                "startLine": 1,
                "endLine": 5,
                "children": [{
                    "type": "BEGIN",
                    "startLine": 2,
                    "endLine": 4,
                    "children": [{
                        "type": "CALL",
                        "name": "target",
                        "startLine": 3,
                        "endLine": 3,
                        "children": [],
                    }],
                }],
            },
            {
                "type": "PROCEDURE",
                "name": "target",
                "startLine": 6,
                "endLine": 10,
                "children": [],
            },
        ],
        "evidence": {
            "version": "2.0.0",
            "language": "postgresql",
            "frontendSchema": "antlr-postgresql/v1",
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
            "facts": facts,
            "grammarRules": ["createfunctionstmt", "functionCall"],
            "presences": [{
                "status": "active",
                "condition": None,
                "configurationId": None,
                "provenance": "unconditional",
            }],
            "completeness": completeness,
        },
    }


def test_dbms_projects_parser_call_facts_and_resolves_with_one_common_core() -> None:
    source, raw = _artifact()
    with TemporaryDirectory() as temporary:
        root = Path(temporary)
        (root / "sample.sql").write_text(source, encoding="utf-8", newline="")
        artifact = root / "sample.json"
        artifact.write_text(json.dumps(raw), encoding="utf-8")
        result = read_antlr_json(
            artifact,
            root,
            source_language="postgresql",
        )

    run = next(
        node for node in result.code_nodes
        if node.label == "PROCEDURE" and node.name == "run"
    )
    target = next(
        node for node in result.code_nodes
        if node.label == "PROCEDURE" and node.name == "target"
    )
    assert len(result.call_sites) == 1
    assert result.call_sites[0]["caller_id"] == run.id
    assert result.call_sites[0]["callee_path_components"] == ["app", "target"]
    assert result.call_sites[0]["call_source"] == "app.target()"
    assert {
        fact["terminal_name"]: fact["code_node_id"]
        for fact in result.callable_facts
    } == {"run": run.id, "target": target.id}

    classified = classify_call_sites(
        result.call_sites,
        result.code_nodes,
        callable_facts=result.callable_facts,
        evidence_sources=result.evidence_sources,
        evidence_completeness=result.evidence_completeness,
    )
    assert [(edge.caller_id, edge.callee_id) for edge in classified.edges] == [
        (run.id, target.id),
    ]
    assert classified.ledger[0]["resolution_status"] == "internal"

    runtime = asyncio.run(link_parser_calls_step(
        {
            "call_sites": result.call_sites,
            "code_nodes": result.code_nodes,
            "callable_facts": result.callable_facts,
            "evidence_sources": result.evidence_sources,
            "evidence_completeness": result.evidence_completeness,
        },
        {"configurable": {"strategy": DBMS_PIPELINE}},
    ))
    assert [
        (edge.caller_id, edge.callee_id)
        for edge in runtime["calls_edges"]
    ] == [(run.id, target.id)]
    assert runtime["call_resolution_ledger"][0]["resolution_status"] == "internal"
