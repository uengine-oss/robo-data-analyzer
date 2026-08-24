"""Language-neutral structural expression IR owned by Analyzer.

This IR normalizes frontend grammar vocabulary without claiming final binding,
type, linkage, or runtime meaning.  Exact source coordinates remain provenance.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


ExpressionForm = Literal[
    "root", "primary", "literal", "generic", "postfix", "arguments",
    "unary", "cast", "binary", "conditional", "assignment",
    "declaration_syntax", "statement_syntax", "extension_syntax",
]
TokenCategory = Literal[
    "identifier", "literal", "operator", "punctuation", "keyword",
]


@dataclass(frozen=True)
class SemanticSourceRange:
    char_offset: int
    char_length: int

    @property
    def end(self) -> int:
        return self.char_offset + self.char_length

    def contains(self, other: "SemanticSourceRange") -> bool:
        return self.char_offset <= other.char_offset and other.end <= self.end

    def as_record(self) -> dict[str, int]:
        return {"char_offset": self.char_offset, "char_length": self.char_length}


@dataclass(frozen=True)
class SemanticExpressionToken:
    category: TokenCategory
    text: str
    source_range: SemanticSourceRange

    def as_record(self) -> dict:
        return {
            "category": self.category,
            "text": self.text,
            "source_range": self.source_range.as_record(),
        }


@dataclass(frozen=True)
class SemanticExpressionNode:
    form: ExpressionForm
    source_range: SemanticSourceRange
    tokens: tuple[SemanticExpressionToken, ...] = ()
    children: tuple["SemanticExpressionNode", ...] = ()

    def __post_init__(self) -> None:
        for token in self.tokens:
            if not self.source_range.contains(token.source_range):
                raise ValueError("semantic token is outside its node range")
        for child in self.children:
            if not self.source_range.contains(child.source_range):
                raise ValueError("semantic child is outside its node range")

    def ordered_tokens(self) -> tuple[SemanticExpressionToken, ...]:
        tokens = list(self.tokens)
        for child in self.children:
            tokens.extend(child.ordered_tokens())
        tokens.sort(key=lambda item: (
            item.source_range.char_offset, item.source_range.char_length,
        ))
        coordinates = [
            (item.source_range.char_offset, item.source_range.char_length)
            for item in tokens
        ]
        if len(coordinates) != len(set(coordinates)):
            raise ValueError("semantic expression token coordinate is not exact-once")
        return tuple(tokens)

    def as_record(self) -> dict:
        return {
            "form": self.form,
            "source_range": self.source_range.as_record(),
            "tokens": [item.as_record() for item in self.tokens],
            "children": [item.as_record() for item in self.children],
        }


@dataclass(frozen=True)
class SemanticExpressionFact:
    fact_id: str
    source_id: str
    code_node_id: str
    role: str
    source_range: SemanticSourceRange
    root: SemanticExpressionNode

    def __post_init__(self) -> None:
        if self.root.source_range != self.source_range:
            raise ValueError("semantic expression root must equal its fact range")

    def as_record(self) -> dict:
        return {
            "fact_id": self.fact_id,
            "source_id": self.source_id,
            "code_node_id": self.code_node_id,
            "role": self.role,
            "source_range": self.source_range.as_record(),
            "root": self.root.as_record(),
        }
