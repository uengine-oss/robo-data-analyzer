"""Normalize Analyzer expression trees into strict language-neutral variants."""
from __future__ import annotations

from pipeline.models.discriminated_expression_ir import (
    AssignmentExpression,
    BinaryExpression,
    CallExpression,
    CastExpression,
    ConditionalExpression,
    GroupExpression,
    LiteralExpression,
    MemberExpression,
    ReferenceExpression,
    ResolvedExpression,
    ResolvedExpressionFact,
    SequenceExpression,
    SubscriptExpression,
    UnaryExpression,
)
from pipeline.models.semantic_expression_ir import (
    SemanticExpressionFact,
    SemanticExpressionNode,
    SemanticExpressionToken,
    SemanticSourceRange,
)


class ExpressionSemanticError(ValueError):
    """A structural expression cannot be classified without guessing."""


_OPERATORS = {
    "==": "equal", "!=": "not_equal", "<": "less", "<=": "less_equal",
    ">": "greater", ">=": "greater_equal", "&&": "logical_and",
    "||": "logical_or", "+": "add", "-": "subtract", "*": "multiply",
    "/": "divide", "%": "modulo", "<<": "shift_left", ">>": "shift_right",
    "&": "bitwise_and", "|": "bitwise_or", "^": "bitwise_xor",
    "=": "assign", "*=": "multiply_assign", "/=": "divide_assign",
    "%=": "modulo_assign", "+=": "add_assign", "-=": "subtract_assign",
    "<<=": "shift_left_assign", ">>=": "shift_right_assign",
    "&=": "bitwise_and_assign", "^=": "bitwise_xor_assign",
    "|=": "bitwise_or_assign", "!": "logical_not", "~": "bitwise_not",
    "++": "increment", "--": "decrement", "sizeof": "sizeof",
}


def _semantic_operator(token: SemanticExpressionToken) -> str:
    value = _OPERATORS.get(token.text)
    if value is None:
        raise ExpressionSemanticError(f"unsupported expression operator {token.text!r}")
    return value


def _operator_tokens(node: SemanticExpressionNode) -> tuple[SemanticExpressionToken, ...]:
    return tuple(token for token in node.tokens if token.category in {"operator", "keyword"})


def _collapsed(node: SemanticExpressionNode) -> ResolvedExpression:
    syntax_wrapper = node.form in {
        "declaration_syntax", "statement_syntax", "extension_syntax",
    } and all(token.category == "punctuation" for token in node.tokens)
    if len(node.children) != 1 or (node.tokens and not syntax_wrapper):
        raise ExpressionSemanticError(
            "expression wrapper cannot be collapsed exactly: "
            f"form={node.form!r} "
            f"tokens={tuple(token.text for token in node.tokens)!r} "
            f"children={len(node.children)}"
        )
    wrappers: list[SemanticSourceRange] = []
    seen: set[int] = set()
    current = node
    while len(current.children) == 1 and (
        not current.tokens
        or (
            current.form in {
                "declaration_syntax", "statement_syntax", "extension_syntax",
            }
            and all(token.category == "punctuation" for token in current.tokens)
        )
    ):
        identity = id(current)
        if identity in seen:
            raise ExpressionSemanticError("semantic expression contains a child cycle")
        seen.add(identity)
        wrappers.append(current.source_range)
        current = current.children[0]

    resolved = _resolve(current)
    for source_range in reversed(wrappers):
        if resolved.source_range != source_range:
            resolved = GroupExpression(source_range, resolved)
    return resolved


def _primary(node: SemanticExpressionNode) -> ResolvedExpression:
    if not node.tokens and len(node.children) == 1:
        return _collapsed(node)
    if len(node.tokens) == 1 and not node.children:
        token = node.tokens[0]
        if token.category == "identifier":
            return ReferenceExpression(node.source_range, token.text)
        if token.category == "literal":
            return LiteralExpression(node.source_range, token.text)
    punctuation = tuple(token.text for token in node.tokens)
    if punctuation == ("(", ")") and len(node.children) == 1:
        return GroupExpression(node.source_range, _resolve(node.children[0]))
    raise ExpressionSemanticError(
        f"unsupported primary expression tokens={punctuation!r} children={len(node.children)}"
    )


def _arguments(node: SemanticExpressionNode) -> tuple[ResolvedExpression, ...]:
    if node.form != "arguments":
        raise ExpressionSemanticError(f"expected arguments node, got {node.form!r}")
    separators = [token.text for token in node.tokens]
    if any(value != "," for value in separators) or len(separators) not in {
        max(len(node.children) - 1, 0), 0,
    }:
        raise ExpressionSemanticError("argument separators do not match child population")
    return tuple(_resolve(child) for child in node.children)


def _member_chain(
    node: SemanticExpressionNode,
    base: ResolvedExpression,
    tokens: tuple[SemanticExpressionToken, ...],
) -> tuple[ResolvedExpression, tuple[SemanticExpressionToken, ...]]:
    cursor = 0
    current = base
    while cursor + 1 < len(tokens) and tokens[cursor].text in {".", "->"}:
        operator, member = tokens[cursor], tokens[cursor + 1]
        if member.category != "identifier":
            raise ExpressionSemanticError("member access requires an identifier token")
        current = MemberExpression(
            SemanticSourceRange(
                current.source_range.char_offset,
                member.source_range.end - current.source_range.char_offset,
            ),
            "pointer" if operator.text == "->" else "direct",
            current,
            member.text,
        )
        cursor += 2
    return current, tokens[cursor:]


def _postfix(node: SemanticExpressionNode) -> ResolvedExpression:
    if not node.children:
        raise ExpressionSemanticError("postfix expression requires a base child")
    current = _resolve(node.children[0])
    tokens = tuple(sorted(node.tokens, key=lambda item: item.source_range.char_offset))
    current, tokens = _member_chain(node, current, tokens)
    remaining_children = list(node.children[1:])
    spellings = tuple(token.text for token in tokens)
    if spellings == ("(", ")"):
        if len(remaining_children) == 1 and remaining_children[0].form == "arguments":
            arguments = _arguments(remaining_children.pop())
        else:
            # Some structurally equivalent frontend versions expose each argument as a
            # direct postfix child instead of inserting an arguments container.
            arguments = tuple(_resolve(child) for child in remaining_children)
            remaining_children.clear()
        current = CallExpression(node.source_range, current, arguments)
        tokens = ()
    elif spellings == ("[", "]"):
        if len(remaining_children) != 1:
            raise ExpressionSemanticError("subscript expression requires one index")
        current = SubscriptExpression(
            node.source_range, current, _resolve(remaining_children.pop()),
        )
        tokens = ()
    elif spellings in (("++",), ("--",)):
        if remaining_children:
            raise ExpressionSemanticError("postfix update has unexpected children")
        current = UnaryExpression(
            node.source_range,
            "post_increment" if spellings[0] == "++" else "post_decrement",
            current,
        )
        tokens = ()
    if tokens or remaining_children:
        raise ExpressionSemanticError(
            f"unsupported postfix suffix tokens={tuple(t.text for t in tokens)!r} "
            f"children={len(remaining_children)}"
        )
    if current.source_range != node.source_range:
        current = GroupExpression(node.source_range, current)
    return current


def _unary(node: SemanticExpressionNode) -> ResolvedExpression:
    operators = _operator_tokens(node)
    if not operators:
        return _collapsed(node)
    if len(operators) != 1 or len(node.children) != 1:
        raise ExpressionSemanticError("unary expression requires one operator and operand")
    return UnaryExpression(
        node.source_range, _semantic_operator(operators[0]), _resolve(node.children[0]),
    )


def _binary(node: SemanticExpressionNode) -> ResolvedExpression:
    operators = _operator_tokens(node)
    if not operators:
        return _collapsed(node)
    if len(node.children) != len(operators) + 1:
        raise ExpressionSemanticError("binary operator/operand population mismatch")
    semantic = tuple(_semantic_operator(token) for token in operators)
    operands = tuple(_resolve(child) for child in node.children)
    if len(set(semantic)) == 1:
        return BinaryExpression(node.source_range, semantic[0], operands)
    current = operands[0]
    for operator, right in zip(semantic, operands[1:], strict=True):
        current = BinaryExpression(
            SemanticSourceRange(
                current.source_range.char_offset,
                right.source_range.end - current.source_range.char_offset,
            ),
            operator,
            (current, right),
        )
    if current.source_range != node.source_range:
        current = GroupExpression(node.source_range, current)
    return current


def _assignment(node: SemanticExpressionNode) -> ResolvedExpression:
    operators = _operator_tokens(node)
    if not operators:
        return _collapsed(node)
    if len(operators) != 1 or len(node.children) != 2:
        raise ExpressionSemanticError("assignment requires target, operator and value")
    return AssignmentExpression(
        node.source_range,
        _semantic_operator(operators[0]),
        _resolve(node.children[0]),
        _resolve(node.children[1]),
    )


def _conditional(node: SemanticExpressionNode) -> ResolvedExpression:
    spellings = tuple(token.text for token in node.tokens)
    if not spellings:
        return _collapsed(node)
    if spellings != ("?", ":") or len(node.children) != 3:
        raise ExpressionSemanticError("conditional expression shape is invalid")
    return ConditionalExpression(
        node.source_range,
        _resolve(node.children[0]),
        _resolve(node.children[1]),
        _resolve(node.children[2]),
    )


def _cast(node: SemanticExpressionNode) -> ResolvedExpression:
    if not node.tokens:
        return _collapsed(node)
    if tuple(token.text for token in node.tokens) != ("(", ")") or len(node.children) != 2:
        raise ExpressionSemanticError("cast expression shape is invalid")
    type_node, value_node = node.children
    type_tokens = tuple(token.text for token in type_node.ordered_tokens())
    if not type_tokens:
        raise ExpressionSemanticError("cast type has no structural tokens")
    return CastExpression(
        node.source_range,
        type_node.source_range,
        type_tokens,
        _resolve(value_node),
    )


def _sequence(node: SemanticExpressionNode) -> ResolvedExpression:
    separators = tuple(token.text for token in node.tokens)
    if not separators:
        return _collapsed(node)
    if any(token != "," for token in separators) or len(node.children) != len(separators) + 1:
        raise ExpressionSemanticError("expression sequence population mismatch")
    return SequenceExpression(node.source_range, tuple(_resolve(child) for child in node.children))


def _syntax(node: SemanticExpressionNode) -> ResolvedExpression:
    spellings = tuple(token.text for token in node.tokens)
    if spellings and all(token == "," for token in spellings):
        return _sequence(node)
    return _collapsed(node)


def _resolve(node: SemanticExpressionNode) -> ResolvedExpression:
    if node.form == "primary":
        return _primary(node)
    if node.form == "literal":
        return _primary(SemanticExpressionNode(
            "primary", node.source_range, node.tokens, node.children,
        ))
    if node.form == "postfix":
        return _postfix(node)
    if node.form == "arguments":
        raise ExpressionSemanticError("argument list cannot be a standalone expression")
    if node.form == "unary":
        return _unary(node)
    if node.form == "binary":
        return _binary(node)
    if node.form == "assignment":
        return _assignment(node)
    if node.form == "conditional":
        return _conditional(node)
    if node.form == "cast":
        return _cast(node)
    if node.form == "root":
        return _sequence(node)
    if node.form in {"declaration_syntax", "statement_syntax", "extension_syntax"}:
        return _syntax(node)
    raise ExpressionSemanticError(f"unsupported semantic expression form {node.form!r}")


def resolve_semantic_expression(
    fact: SemanticExpressionFact,
    *,
    start_line: int,
    end_line: int,
) -> ResolvedExpressionFact:
    expression = _resolve(fact.root)
    if expression.source_range != fact.source_range:
        expression = GroupExpression(fact.source_range, expression)
    return ResolvedExpressionFact(
        fact_id=fact.fact_id,
        source_id=fact.source_id,
        code_node_id=fact.code_node_id,
        role=fact.role,
        start_line=start_line,
        end_line=end_line,
        source_range=fact.source_range,
        expression=expression,
    )
