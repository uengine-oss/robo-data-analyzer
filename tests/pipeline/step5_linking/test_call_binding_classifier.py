"""Formal contract for IR 2.0 adapter-owned semantic resolution (C-080)."""
from __future__ import annotations

import pytest

from graph.schema.graph_nodes import CodeNode
from pipeline.steps.step5_linking.link_calls.call_classifier import (
    CallClassificationInvariantError,
    classify_call_sites,
)


def _node(node_id: str, name: str, file_path: str) -> CodeNode:
    return CodeNode(
        id=node_id, name=name, owner_id=f"code:{file_path}",
        signature=f"int {name}(int value)", file_path=file_path,
        start_line=1, end_line=2, label="FUNCTION",
    )


def _range(start: int, length: int) -> dict[str, int]:
    return {"char_offset": start, "char_length": length}


def _scope(source_length: int = 200) -> list[dict]:
    return [{"kind": "translation_unit", "range": _range(0, source_length)}]


def _component(rule: str, tokens: list[tuple[str, str, int]]) -> dict:
    return {
        "grammar_rule": rule,
        "range": _range(min(item[2] for item in tokens), 20),
        "direct_tokens": [
            {"token_kind": kind, "text": text, "range": _range(offset, len(text))}
            for kind, text, offset in tokens
        ],
        "children": [],
    }


def _callable(
    fact_id: str,
    name: str,
    source_id: str,
    *,
    role: str,
    static: bool = False,
    code_node_id: str | None = None,
    declaration_point: int = 30,
) -> dict:
    name_start = 10
    specifiers = [_component("typeSpecifier", [("Int", "int", 4)])]
    if static:
        specifiers.insert(0, _component(
            "storageClassSpecifier", [("Static", "static", 0)],
        ))
    return {
        "fact_id": fact_id,
        "role": role,
        "code_node_id": code_node_id,
        "source_id": source_id,
        "range": _range(0, 40),
        "name_range": _range(name_start, len(name)),
        "terminal_name": name,
        "ast_node_range": _range(0, 40) if role == "definition" else None,
        "scope_path": _scope(),
        "scope_range": _range(0, 200),
        "declaration_point": declaration_point,
        "syntax": {
            "schema": "c-callable-syntax/v1",
            "declaration_specifiers": specifiers,
            "declarator": _component(
                "declarator", [("Identifier", name, name_start)],
            ),
            "attributes": [],
        },
        "grammar_rule": "functionDefinition" if role == "definition" else "declaration",
        "presence_status": "active",
        "presence": {"status": "active"},
    }


def _call(call_id: str, caller: CodeNode, name: str, *, kind: str = "named") -> dict:
    terminal = "" if kind == "expression" else name
    return {
        "call_id": call_id,
        "caller_id": caller.id,
        "owner_status": "resolved",
        "call_text": terminal,
        "callee_kind": kind,
        "source_id": caller.file_path,
        "presence_status": "active",
        "presence": {"status": "active"},
        "file": caller.file_path,
        "line": 2,
        "range": _range(50, 10),
        "callee_range": _range(50, 6),
        "receiver_range": None,
        "argument_ranges": [],
        "scope_path": _scope(),
        "grammar_rule": "postfixExpression",
    }


def _context(*source_ids: str, language: str = "c") -> tuple[list[dict], list[dict]]:
    sources = [
        {
            "source_id": source_id,
            "language": language,
            "frontend_schema": "antlr-c/v1" if language == "c" else "antlr-python/v1",
        }
        for source_id in source_ids
    ]
    completeness = [
        {
            "source_id": source_id, "kind": kind, "status": "complete",
            "population": 1, "emitted": 1, "explicitlyUnresolved": 0,
        }
        for source_id in source_ids for kind in ("call", "callable")
    ]
    return sources, completeness


def _classify(calls, nodes, callables, *source_ids, language: str = "c"):
    sources, completeness = _context(*source_ids, language=language)
    return classify_call_sites(
        calls, nodes, callable_facts=callables,
        evidence_sources=sources, evidence_completeness=completeness,
    )


def test_visible_external_declaration_joins_one_compatible_definition() -> None:
    caller = _node("code:a.c:caller", "caller", "a.c")
    target = _node("code:b.c:target", "target", "b.c")
    result = _classify(
        [_call("call-1", caller, "target")], [caller, target],
        [
            _callable("decl-1", "target", "a.c", role="declaration"),
            _callable(
                "def-1", "target", "b.c", role="definition",
                code_node_id=target.id,
            ),
        ],
        "a.c", "b.c",
    )

    assert [(edge.callee_id, edge.scope) for edge in result.edges] == [
        (target.id, "internal"),
    ]
    assert result.ledger[0]["resolution_status"] == "internal"
    assert result.resolution_counts == {"internal": 1}


def test_same_name_without_visible_declaration_is_external_without_stub_edge() -> None:
    caller = _node("code:a.c:caller", "caller", "a.c")
    same_name = _node("code:b.c:target", "target", "b.c")
    result = _classify(
        [_call("call-2", caller, "target")], [caller, same_name],
        [_callable(
            "def-2", "target", "b.c", role="definition",
            code_node_id=same_name.id,
        )],
        "a.c", "b.c",
    )

    assert result.edges == []
    assert result.ledger[0]["resolution_status"] == "external"
    assert len(result.ledger[0]["external_identity_key"]) == 64


def test_duplicate_compatible_definitions_are_ambiguous_not_first_sorted() -> None:
    caller = _node("code:a.c:caller", "caller", "a.c")
    first = _node("code:b.c:target", "target", "b.c")
    second = _node("code:c.c:target", "target", "c.c")
    result = _classify(
        [_call("call-3", caller, "target")], [caller, first, second],
        [
            _callable("decl-3", "target", "a.c", role="declaration"),
            _callable("def-3a", "target", "b.c", role="definition", code_node_id=first.id),
            _callable("def-3b", "target", "c.c", role="definition", code_node_id=second.id),
        ],
        "a.c", "b.c", "c.c",
    )

    assert result.edges == []
    assert result.ledger[0]["resolution_status"] == "ambiguous"
    assert result.ledger[0]["candidate_fact_ids"] == ["def-3a", "def-3b"]


def test_static_linkage_does_not_cross_translation_units() -> None:
    caller = _node("code:a.c:caller", "caller", "a.c")
    other = _node("code:b.c:target", "target", "b.c")
    result = _classify(
        [_call("call-4", caller, "target")], [caller, other],
        [
            _callable("decl-4", "target", "a.c", role="declaration", static=True),
            _callable(
                "def-4", "target", "b.c", role="definition",
                static=True, code_node_id=other.id,
            ),
        ],
        "a.c", "b.c",
    )

    assert result.edges == []
    assert result.ledger[0]["resolution_status"] == "external"


def test_expression_callee_is_dynamic_without_name_guessing() -> None:
    caller = _node("code:a.c:caller", "caller", "a.c")
    result = _classify(
        [_call("call-5", caller, "", kind="expression")], [caller], [], "a.c",
    )

    assert result.edges == []
    assert result.ledger[0]["resolution_status"] == "dynamic"


def test_unregistered_language_is_unsupported_without_name_fallback() -> None:
    caller = _node("code:a.py:caller", "caller", "a.py")
    same_name = _node("code:b.py:target", "target", "b.py")
    result = _classify(
        [_call("call-6", caller, "target")], [caller, same_name], [],
        "a.py", "b.py", language="python",
    )

    assert result.edges == []
    assert result.ledger[0]["resolution_status"] == "unsupported"
    assert result.ledger[0]["reason"] == "unsupported_language_adapter"


def test_partial_callable_evidence_fails_closed_at_affected_source() -> None:
    caller = _node("code:a.c:caller", "caller", "a.c")
    sources, completeness = _context("a.c")
    callable_row = next(row for row in completeness if row["kind"] == "callable")
    callable_row.update({
        "status": "partial", "population": 1, "emitted": 0,
        "explicitlyUnresolved": 1,
        "reason": "insufficient_parser_recovery",
        "reasons": ["insufficient_parser_recovery"],
    })
    result = classify_call_sites(
        [_call("call-7", caller, "target")], [caller], callable_facts=[],
        evidence_sources=sources, evidence_completeness=completeness,
    )

    assert result.ledger[0]["resolution_status"] == "unsupported"
    assert result.ledger[0]["reason"] == "insufficient_parser_recovery"


def test_duplicate_call_id_rejects_before_resolution() -> None:
    caller = _node("code:a.c:caller", "caller", "a.c")
    call = _call("duplicate", caller, "target")
    sources, completeness = _context("a.c")

    with pytest.raises(CallClassificationInvariantError, match="exact-once"):
        classify_call_sites(
            [call, dict(call)], [caller], evidence_sources=sources,
            evidence_completeness=completeness,
        )
