"""Strict language-neutral expression semantics derived from structural frontend IR.

The variants preserve exact source ranges while naming only syntax-level expression
semantics.  Binding, type, linkage, overload and runtime meaning remain unresolved until
their Analyzer-owned resolution stages provide evidence.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, TypeAlias

from pipeline.models.semantic_expression_ir import SemanticSourceRange


class ExpressionValue(Protocol):
    source_range: SemanticSourceRange

    def as_record(self) -> dict: ...


def _base(kind: str, source_range: SemanticSourceRange) -> dict:
    return {"kind": kind, "source_range": source_range.as_record()}


@dataclass(frozen=True)
class ReferenceExpression:
    source_range: SemanticSourceRange
    name: str

    def as_record(self) -> dict:
        return {**_base("reference", self.source_range), "name": self.name}


@dataclass(frozen=True)
class LiteralExpression:
    source_range: SemanticSourceRange
    spelling: str

    def as_record(self) -> dict:
        return {**_base("literal", self.source_range), "spelling": self.spelling}


@dataclass(frozen=True)
class GroupExpression:
    source_range: SemanticSourceRange
    value: "ResolvedExpression"

    def as_record(self) -> dict:
        return {**_base("group", self.source_range), "value": self.value.as_record()}


@dataclass(frozen=True)
class MemberExpression:
    source_range: SemanticSourceRange
    access: str
    base: "ResolvedExpression"
    member: str

    def as_record(self) -> dict:
        return {
            **_base("member", self.source_range),
            "access": self.access,
            "base": self.base.as_record(),
            "member": self.member,
        }


@dataclass(frozen=True)
class SubscriptExpression:
    source_range: SemanticSourceRange
    base: "ResolvedExpression"
    index: "ResolvedExpression"

    def as_record(self) -> dict:
        return {
            **_base("subscript", self.source_range),
            "base": self.base.as_record(),
            "index": self.index.as_record(),
        }


@dataclass(frozen=True)
class CallExpression:
    source_range: SemanticSourceRange
    callee: "ResolvedExpression"
    arguments: tuple["ResolvedExpression", ...]

    def as_record(self) -> dict:
        return {
            **_base("call", self.source_range),
            "callee": self.callee.as_record(),
            "arguments": [item.as_record() for item in self.arguments],
        }


@dataclass(frozen=True)
class UnaryExpression:
    source_range: SemanticSourceRange
    operator: str
    operand: "ResolvedExpression"

    def as_record(self) -> dict:
        return {
            **_base("unary", self.source_range),
            "operator": self.operator,
            "operand": self.operand.as_record(),
        }


@dataclass(frozen=True)
class BinaryExpression:
    source_range: SemanticSourceRange
    operator: str
    operands: tuple["ResolvedExpression", ...]

    def as_record(self) -> dict:
        return {
            **_base("binary", self.source_range),
            "operator": self.operator,
            "operands": [item.as_record() for item in self.operands],
        }


@dataclass(frozen=True)
class ConditionalExpression:
    source_range: SemanticSourceRange
    condition: "ResolvedExpression"
    when_true: "ResolvedExpression"
    when_false: "ResolvedExpression"

    def as_record(self) -> dict:
        return {
            **_base("conditional", self.source_range),
            "condition": self.condition.as_record(),
            "when_true": self.when_true.as_record(),
            "when_false": self.when_false.as_record(),
        }


@dataclass(frozen=True)
class AssignmentExpression:
    source_range: SemanticSourceRange
    operator: str
    target: "ResolvedExpression"
    value: "ResolvedExpression"

    def as_record(self) -> dict:
        return {
            **_base("assignment", self.source_range),
            "operator": self.operator,
            "target": self.target.as_record(),
            "value": self.value.as_record(),
        }


@dataclass(frozen=True)
class CastExpression:
    source_range: SemanticSourceRange
    type_range: SemanticSourceRange
    type_tokens: tuple[str, ...]
    value: "ResolvedExpression"

    def as_record(self) -> dict:
        return {
            **_base("cast", self.source_range),
            "type_range": self.type_range.as_record(),
            "type_tokens": list(self.type_tokens),
            "value": self.value.as_record(),
        }


@dataclass(frozen=True)
class SequenceExpression:
    source_range: SemanticSourceRange
    values: tuple["ResolvedExpression", ...]

    def as_record(self) -> dict:
        return {
            **_base("sequence", self.source_range),
            "values": [item.as_record() for item in self.values],
        }


ResolvedExpression: TypeAlias = (
    ReferenceExpression
    | LiteralExpression
    | GroupExpression
    | MemberExpression
    | SubscriptExpression
    | CallExpression
    | UnaryExpression
    | BinaryExpression
    | ConditionalExpression
    | AssignmentExpression
    | CastExpression
    | SequenceExpression
)


@dataclass(frozen=True)
class ResolvedExpressionFact:
    fact_id: str
    source_id: str
    code_node_id: str
    role: str
    source_range: SemanticSourceRange
    expression: ResolvedExpression

    def __post_init__(self) -> None:
        if self.expression.source_range != self.source_range:
            raise ValueError("resolved expression root must equal its fact range")

    def as_record(self) -> dict:
        return {
            "fact_id": self.fact_id,
            "source_id": self.source_id,
            "code_node_id": self.code_node_id,
            "role": self.role,
            "source_range": self.source_range.as_record(),
            "expression": self.expression.as_record(),
        }
