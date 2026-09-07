"""XML span adapter : stdlib expat, exact element spans, fail-closed.

Semantic unit = outermost element carrying an identifier; nested identifier
elements are absorbed into the outer chunk's key list.  An identifier is an
``id``/``name`` attribute, or the text of a direct child element whose tag
contains ``name`` (``<ejb-name>``, ``<servlet-name>`` …).

Security: a bare DOCTYPE (as in every J2EE deployment descriptor) is allowed,
but ENTITY declarations are rejected before expansion (billion laughs) and
external entities are never resolved (XXE) — all with stdlib expat only.
"""
from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass, field
from xml.parsers import expat

from pipeline.stages.external.parsers.key_roles import (
    is_addressable_attribute,
    is_addressable_child_text,
)
from pipeline.stages.external.parsers.span_common import (
    AdapterParseError,
    SemanticSpan,
    is_reference_key,
    sorted_unique_keys,
)


_KEY_ATTRS = ("id", "name")


@dataclass
class _Open:
    tag: str
    start: int                               # byte offset of '<'
    tag_close: int                           # byte offset just past start tag '>'
    self_closing: bool
    keys: list[str]
    addressable: list[str] = field(default_factory=list)
    text: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class _Element:
    start: int
    end: int
    keys: tuple[str, ...]
    name: str
    addressable: tuple[str, ...] = ()


def _tag_end(data: bytes, start: int) -> int:
    """Offset just past the tag's '>' using a quote-aware scan."""
    quote = b""
    index = start
    while index < len(data):
        char = data[index:index + 1]
        if quote:
            if char == quote:
                quote = b""
        elif char in (b'"', b"'"):
            quote = char
        elif char == b">":
            return index + 1
        index += 1
    raise AdapterParseError("unterminated XML tag")


def parse_xml_spans(source: str) -> list[SemanticSpan]:
    """Return spans of outermost identifier-bearing elements, in source order."""
    data = source.encode("utf-8")
    parser = expat.ParserCreate("utf-8")

    namespace = ""
    stack: list[_Open] = []
    elements: list[_Element] = []

    def on_entity_decl(*_args) -> None:
        raise AdapterParseError("ENTITY declarations are not allowed in external XML")

    def on_external_entity(*_args) -> int:
        return 0                             # refuse resolution -> expat aborts

    def on_start(tag: str, attrs: dict[str, str]) -> None:
        nonlocal namespace
        if not namespace:
            namespace = (attrs.get("namespace") or "").strip()
        start = parser.CurrentByteIndex
        tag_close = _tag_end(data, start)
        keys: list[str] = []
        addressable: list[str] = []
        for attribute in _KEY_ATTRS:
            value = (attrs.get(attribute) or "").strip()
            if not is_reference_key(value):
                continue
            # 이 자리가 조회 주소인지는 요소·속성 조합이 정한다 (key_roles 표).
            is_address = is_addressable_attribute(tag, attribute)
            keys.append(value)
            if is_address:
                addressable.append(value)
            if attribute == "id" and namespace:
                qualified = f"{namespace}.{value}"
                if is_reference_key(qualified):
                    keys.append(qualified)
                    if is_address:
                        addressable.append(qualified)
        stack.append(_Open(
            tag=tag,
            start=start,
            tag_close=tag_close,
            self_closing=data[tag_close - 2:tag_close] == b"/>",
            keys=keys,
            addressable=addressable,
        ))

    def on_text(text: str) -> None:
        if stack:
            stack[-1].text.append(text)

    def on_end(tag: str) -> None:
        node = stack.pop()
        end = node.tag_close if node.self_closing else _tag_end(data, parser.CurrentByteIndex)
        if stack and "name" in node.tag.rsplit(":", 1)[-1].lower():
            own_text = "".join(node.text).strip()
            if is_reference_key(own_text):
                stack[-1].keys.append(own_text)
                # 같은 태그라도 정의 자리(<session>)면 주소, 언급 자리(<method>)면 아니다.
                if is_addressable_child_text(stack[-1].tag, node.tag):
                    stack[-1].addressable.append(own_text)
        if node.keys:
            elements.append(_Element(
                start=node.start, end=end, keys=tuple(node.keys), name=node.keys[0],
                addressable=tuple(node.addressable)))

    parser.EntityDeclHandler = on_entity_decl
    parser.ExternalEntityRefHandler = on_external_entity
    parser.StartElementHandler = on_start
    parser.EndElementHandler = on_end
    parser.CharacterDataHandler = on_text
    try:
        parser.Parse(data, True)
    except expat.ExpatError as error:
        raise AdapterParseError(f"invalid XML: {error}") from error

    # Outermost identifier element wins; absorbed descendants contribute keys.
    elements.sort(key=lambda element: (element.start, -element.end))
    kept: list[_Element] = []
    merged_keys: list[list[str]] = []
    merged_addressable: list[list[str]] = []
    for element in elements:
        if kept and element.end <= kept[-1].end:
            merged_keys[-1].extend(element.keys)
            merged_addressable[-1].extend(element.addressable)
            continue
        kept.append(element)
        merged_keys.append(list(element.keys))
        merged_addressable.append(list(element.addressable))

    # expat indexes bytes; spans are stored as char offsets of the decoded source.
    byte_offsets = [0]
    for char in source:
        byte_offsets.append(byte_offsets[-1] + len(char.encode("utf-8")))

    def to_char(byte_offset: int) -> int:
        index = bisect_right(byte_offsets, byte_offset) - 1
        if byte_offsets[index] != byte_offset:
            raise AdapterParseError("XML span does not align with a character boundary")
        return index

    return [
        SemanticSpan(
            char_start=to_char(element.start),
            char_end=to_char(element.end),
            keys=sorted_unique_keys(keys),
            semantic_kind="xml_element",
            name=element.name,
            addressable_keys=sorted_unique_keys(addressable),
        )
        for element, keys, addressable in zip(kept, merged_keys, merged_addressable)
    ]
