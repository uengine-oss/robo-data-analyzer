from __future__ import annotations

from pipeline.models.semantic_expression_ir import (
    SemanticExpressionFact,
    SemanticExpressionNode,
    SemanticExpressionToken,
    SemanticSourceRange,
)
from pipeline.parsing.resolution.expression_semantic_core import (
    resolve_semantic_expression,
)


def _range(start: int, length: int) -> SemanticSourceRange:
    return SemanticSourceRange(start, length)


def _token(category: str, text: str, start: int) -> SemanticExpressionToken:
    return SemanticExpressionToken(category, text, _range(start, len(text)))


def _node(
    form: str,
    start: int,
    length: int,
    *,
    tokens: tuple[SemanticExpressionToken, ...] = (),
    children: tuple[SemanticExpressionNode, ...] = (),
) -> SemanticExpressionNode:
    return SemanticExpressionNode(form, _range(start, length), tokens, children)


def test_resolves_c_call_comparison_without_source_reparsing() -> None:
    receiver = _node(
        "postfix", 12, 12,
        tokens=(_token("operator", "->", 17), _token("identifier", "op_cl", 19)),
        children=(_node(
            "primary", 12, 5,
            tokens=(_token("identifier", "INPUT", 12),),
        ),),
    )
    argument = _node("arguments", 12, 12, children=(receiver,))
    call = _node(
        "postfix", 0, 25,
        tokens=(_token("punctuation", "(", 11), _token("punctuation", ")", 24)),
        children=(
            _node(
                "primary", 0, 11,
                tokens=(_token("identifier", "is_not_null", 0),),
            ),
            argument,
        ),
    )
    right = _node(
        "primary", 29, 6,
        tokens=(_token("identifier", "RC_NRM", 29),),
    )
    root = _node(
        "binary", 0, 35,
        tokens=(_token("operator", "==", 26),),
        children=(call, right),
    )
    fact = SemanticExpressionFact(
        fact_id="expr-1",
        source_id="sample.c",
        code_node_id="code:sample.c:f:if:1",
        role="condition",
        source_range=_range(0, 35),
        root=root,
    )

    resolved = resolve_semantic_expression(fact, start_line=7, end_line=7)

    assert resolved.fact_id == fact.fact_id
    assert resolved.expression.as_record() == {
        "kind": "binary",
        "source_range": {"char_offset": 0, "char_length": 35},
        "operator": "equal",
        "operands": [
            {
                "kind": "call",
                "source_range": {"char_offset": 0, "char_length": 25},
                "callee": {
                    "kind": "reference",
                    "source_range": {"char_offset": 0, "char_length": 11},
                    "name": "is_not_null",
                },
                "arguments": [
                    {
                        "kind": "member",
                        "source_range": {"char_offset": 12, "char_length": 12},
                        "access": "pointer",
                        "base": {
                            "kind": "reference",
                            "source_range": {"char_offset": 12, "char_length": 5},
                            "name": "INPUT",
                        },
                        "member": "op_cl",
                    }
                ],
            },
            {
                "kind": "reference",
                "source_range": {"char_offset": 29, "char_length": 6},
                "name": "RC_NRM",
            },
        ],
    }


def test_collapses_deep_parser_wrapper_chain_without_python_recursion() -> None:
    source_range = _range(0, 1)
    root = _node(
        "primary",
        0,
        1,
        tokens=(_token("identifier", "x", 0),),
    )
    for _ in range(2_000):
        root = _node(
            "binary",
            0,
            1,
            children=(root,),
        )
    fact = SemanticExpressionFact(
        fact_id="deep-wrapper",
        source_id="large.c",
        code_node_id="code:large.c:f",
        role="condition",
        source_range=source_range,
        root=root,
    )

    resolved = resolve_semantic_expression(fact, start_line=1, end_line=1)

    assert resolved.expression.as_record() == {
        "kind": "reference",
        "source_range": {"char_offset": 0, "char_length": 1},
        "name": "x",
    }


def test_collapses_declaration_punctuation_wrapper() -> None:
    child = _node(
        "primary",
        1,
        1,
        tokens=(_token("identifier", "x", 1),),
    )
    root = _node(
        "declaration_syntax",
        0,
        3,
        tokens=(
            _token("punctuation", "{", 0),
            _token("punctuation", "}", 2),
        ),
        children=(child,),
    )
    fact = SemanticExpressionFact(
        fact_id="initializer-wrapper",
        source_id="sample.c",
        code_node_id="code:sample.c:x",
        role="initializer",
        source_range=_range(0, 3),
        root=root,
    )

    resolved = resolve_semantic_expression(fact, start_line=1, end_line=1)

    assert resolved.expression.as_record() == {
        "kind": "group",
        "source_range": {"char_offset": 0, "char_length": 3},
        "value": {
            "kind": "reference",
            "source_range": {"char_offset": 1, "char_length": 1},
            "name": "x",
        },
    }


def test_resolves_declaration_initializer_sequence() -> None:
    left = _node(
        "primary", 0, 1, tokens=(_token("literal", "1", 0),),
    )
    right = _node(
        "primary", 2, 1, tokens=(_token("literal", "2", 2),),
    )
    root = _node(
        "declaration_syntax",
        0,
        3,
        tokens=(_token("punctuation", ",", 1),),
        children=(left, right),
    )
    fact = SemanticExpressionFact(
        fact_id="initializer-sequence",
        source_id="sample.c",
        code_node_id="code:sample.c:x",
        role="initializer",
        source_range=_range(0, 3),
        root=root,
    )

    resolved = resolve_semantic_expression(fact, start_line=1, end_line=1)

    assert resolved.expression.as_record()["kind"] == "sequence"
    assert [item["spelling"] for item in resolved.expression.as_record()["values"]] == ["1", "2"]
