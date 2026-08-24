from __future__ import annotations

from pipeline.models.semantic_expression_ir import (
    SemanticExpressionFact,
    SemanticExpressionNode,
    SemanticExpressionToken,
    SemanticSourceRange,
)
from pipeline.steps.step2_load_ast.expression_semantic_core import (
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

    resolved = resolve_semantic_expression(fact)

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
