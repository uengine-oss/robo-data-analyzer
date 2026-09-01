from __future__ import annotations

import copy

import pytest

from pipeline.parsing.adapters.c_structural_expression_adapter import (
    StructuralExpressionAdapterError,
    adapt_c_structural_expression,
)

from tests.pipeline.parsing.facts.test_common_evidence_ir2 import (
    _artifact,
    _validate,
)


def test_c_adapter_produces_language_neutral_expression_tree() -> None:
    source, raw = _artifact()
    fact = _validate(raw, source).structural_expression_facts[0]

    adapted = adapt_c_structural_expression(fact, frontend_schema="antlr-c/v1")

    assert adapted.fact_id == fact.fact_id
    assert adapted.role == "return_value"
    assert adapted.root.form == "root"
    assert adapted.root.children[0].form == "postfix"
    tokens = adapted.root.ordered_tokens()
    assert [(token.category, token.text) for token in tokens] == [
        ("identifier", "target"),
        ("punctuation", "("),
        ("literal", "1"),
        ("punctuation", ")"),
    ]
    record = adapted.as_record()
    assert "grammar_rule" not in record["root"]
    assert "language" not in record["root"]


def test_c_adapter_fails_closed_for_unknown_grammar_rule() -> None:
    source, raw = _artifact()
    changed = copy.deepcopy(raw)
    changed["evidence"]["facts"][2]["payload"]["syntax"]["root"][
        "grammarRule"
    ] = "futureExpressionRule"
    changed["evidence"]["grammarRules"][2] = "futureExpressionRule"
    fact = _validate(changed, source).structural_expression_facts[0]

    with pytest.raises(StructuralExpressionAdapterError, match="futureExpressionRule"):
        adapt_c_structural_expression(fact, frontend_schema="antlr-c/v1")


def test_c_adapter_rejects_wrong_frontend_without_guessing() -> None:
    source, raw = _artifact()
    fact = _validate(raw, source).structural_expression_facts[0]

    with pytest.raises(StructuralExpressionAdapterError, match="frontend"):
        adapt_c_structural_expression(fact, frontend_schema="tree-sitter-c/v1")
