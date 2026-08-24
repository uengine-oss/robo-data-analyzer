"""C ANTLR structural-expression facts -> Analyzer-neutral expression IR."""
from __future__ import annotations

from pipeline.models.semantic_expression_ir import (
    SemanticExpressionFact,
    SemanticExpressionNode,
    SemanticExpressionToken,
    SemanticSourceRange,
)
from pipeline.steps.step2_load_ast.evidence_ir import (
    StructuralExpressionEvidenceFact,
    SyntaxComponentEvidence,
    SyntaxTokenEvidence,
)


class StructuralExpressionAdapterError(ValueError):
    """The frontend syntax cannot be normalized without guessing."""


_RULE_FORMS = {
    "expression": "root",
    "constantExpression": "root",
    "exprList": "root",
    "forExpression": "root",
    "constant": "literal",
    "enumerationConstant": "primary",
    "predefinedConstant": "literal",
    "primaryExpression": "primary",
    "genericSelection": "generic",
    "genericAssocList": "generic",
    "genericAssociation": "generic",
    "postfixExpression": "postfix",
    "argumentExpressionList": "arguments",
    "unaryExpression": "unary",
    "castExpression": "cast",
    "multiplicativeExpression": "binary",
    "additiveExpression": "binary",
    "shiftExpression": "binary",
    "relationalExpression": "binary",
    "equalityExpression": "binary",
    "andExpression": "binary",
    "exclusiveOrExpression": "binary",
    "inclusiveOrExpression": "binary",
    "logicalAndExpression": "binary",
    "logicalOrExpression": "binary",
    "conditionalExpression": "conditional",
    "assignmentExpression": "assignment",
}

_DECLARATION_RULES = frozenset({
    "declaration", "declarationSpecifiers", "declarationSpecifier",
    "initDeclaratorList", "initDeclarator", "attributeDeclaration",
    "storageClassSpecifier", "typeSpecifier", "structOrUnionSpecifier",
    "structOrUnion", "memberDeclarationList", "memberDeclaration",
    "specifierQualifierList", "typeSpecifierQualifier", "memberDeclaratorList",
    "memberDeclarator", "enumSpecifier", "enumeratorList", "enumerator",
    "enumTypeSpecifier", "atomicTypeSpecifier", "typeofSpecifier",
    "typeofSpecifierArgument", "typeQualifier", "functionSpecifier",
    "alignmentSpecifier", "declarator", "directDeclarator", "pointer",
    "typeQualifierList", "parameterTypeList", "parameterList",
    "parameterDeclaration", "typeName", "abstractDeclarator",
    "directAbstractDeclarator", "typedefName", "initializer",
    "initializerList", "designation", "designatorList", "designator",
    "staticAssertDeclaration", "attributeSpecifierSequence",
    "attributeSpecifier", "attributeList", "attribute", "attributeToken",
    "attributeArgumentClause", "balancedTokenSequence", "balancedToken",
})
_STATEMENT_RULES = frozenset({
    "statement", "labeledStatement", "compoundStatement", "blockItemList",
    "blockItem", "expressionStatement", "selectionStatement",
    "iterationStatement", "forCondition", "forDeclaration", "jumpStatement",
})
_EXTENSION_RULES = frozenset({
    "translationUnit", "externalDeclaration", "functionDefinition",
    "declarationList", "functionBody", "identifierList", "gnuArrayDesignator",
    "gnuIdentifier", "asmArgument", "asmClobbers", "asmDefinition",
    "toplevelAsmArgument", "asmOperand", "asmOperands", "asmQualifier",
    "asmQualifierList", "asmStatement", "asmStringLiteral",
    "gccDeclaratorExtension", "gnuAttribute", "gnuAttributeList",
    "gnuAttributes", "gnuSingleAttribute", "simpleAsmExpr", "vcSpecificModifer",
})

_OPERATOR_TOKENS = frozenset({
    "Less", "LessEqual", "Greater", "GreaterEqual", "LeftShift", "RightShift",
    "Plus", "PlusPlus", "Minus", "MinusMinus", "Star", "Div", "Mod",
    "And", "Or", "AndAnd", "OrOr", "Caret", "Not", "Tilde", "Question",
    "Colon", "Comma", "Assign", "StarAssign", "DivAssign", "ModAssign",
    "PlusAssign", "MinusAssign", "LeftShiftAssign", "RightShiftAssign",
    "AndAssign", "XorAssign", "OrAssign", "Equal", "NotEqual", "Arrow", "Dot",
})
_PUNCTUATION_TOKENS = frozenset({
    "LeftParen", "RightParen", "LeftBracket", "RightBracket", "LeftBrace",
    "RightBrace", "Semi", "Ellipsis",
})
_LITERAL_TOKENS = frozenset({
    "IntegerConstant", "FloatingConstant", "DigitSequence",
    "CharacterConstant", "StringLiteral", "Constant",
})


def _range(value) -> SemanticSourceRange:
    return SemanticSourceRange(value.char_offset, value.char_length)


def _token_category(token: SyntaxTokenEvidence) -> str:
    if token.token_kind == "Identifier":
        return "identifier"
    if token.token_kind in _LITERAL_TOKENS or token.token_kind.endswith("Constant"):
        return "literal"
    if token.token_kind in _OPERATOR_TOKENS:
        return "operator"
    if token.token_kind in _PUNCTUATION_TOKENS:
        return "punctuation"
    return "keyword"


def _form(grammar_rule: str) -> str:
    form = _RULE_FORMS.get(grammar_rule)
    if form is not None:
        return form
    if grammar_rule in _DECLARATION_RULES:
        return "declaration_syntax"
    if grammar_rule in _STATEMENT_RULES:
        return "statement_syntax"
    if grammar_rule in _EXTENSION_RULES:
        return "extension_syntax"
    raise StructuralExpressionAdapterError(
        f"unsupported antlr-c grammar rule {grammar_rule!r}"
    )


def _adapt_component(component: SyntaxComponentEvidence) -> SemanticExpressionNode:
    return SemanticExpressionNode(
        form=_form(component.grammar_rule),
        source_range=_range(component.range),
        tokens=tuple(
            SemanticExpressionToken(
                category=_token_category(token),
                text=token.text,
                source_range=_range(token.range),
            )
            for token in component.direct_tokens
        ),
        children=tuple(_adapt_component(child) for child in component.children),
    )


def adapt_c_structural_expression(
    fact: StructuralExpressionEvidenceFact,
    *,
    frontend_schema: str,
    code_node_id: str = "",
) -> SemanticExpressionFact:
    if frontend_schema != "antlr-c/v1":
        raise StructuralExpressionAdapterError(
            f"unsupported C frontend {frontend_schema!r}"
        )
    if fact.syntax.schema != "c-expression-syntax/v1":
        raise StructuralExpressionAdapterError(
            f"unsupported C expression schema {fact.syntax.schema!r}"
        )
    root = _adapt_component(fact.syntax.root)
    root.ordered_tokens()
    return SemanticExpressionFact(
        fact_id=fact.fact_id,
        source_id=fact.source_id,
        code_node_id=code_node_id,
        role=fact.role,
        source_range=_range(fact.expression_range),
        root=root,
    )
