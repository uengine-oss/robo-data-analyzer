"""Safe, tag-agnostic XML extraction for SQL and artifact chunks."""
from __future__ import annotations

from dataclasses import dataclass
import re

from lxml import etree


_SQL_PATTERN = re.compile(
    r"\b(?:SELECT|WITH|INSERT\s+INTO|UPDATE\s+[A-Za-z_][\w$#.]*\s+SET|"
    r"DELETE\s+FROM|MERGE\s+INTO)\b",
    re.IGNORECASE,
)
_SQL_COMMENTS_AND_STRINGS = re.compile(
    r"--[^\n]*|/\*.*?\*/|'(?:''|[^'])*'|\"(?:\"\"|[^\"])*\"",
    re.DOTALL,
)
_IDENTIFIER_ATTRS = (
    "id", "name", "class", "ref", "bean", "entity-name", "ejb-name",
    "resource", "type", "resultType", "parameterType", "resultMap",
)
_IDENTIFIER_VALUE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_.]*$")
_FQN = re.compile(r"\b([a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)*\.[A-Z]\w*)\b")


@dataclass(frozen=True)
class XmlEntry:
    key: str
    kind: str
    text: str
    start_line: int
    end_line: int
    sql_type: str = ""
    related_fqns: tuple[str, ...] = ()


class XmlParseError(ValueError):
    pass


def _local_tag(element) -> str:
    tag = element.tag
    return tag.split("}")[-1] if isinstance(tag, str) else ""


def _identifier(value: str) -> bool:
    return bool(
        len(value) >= 2
        and not value.startswith(("http", "/"))
        and _IDENTIFIER_VALUE.fullmatch(value)
    )


def _element_text(element) -> str:
    parts = [element.text or ""]
    for child in element:
        if isinstance(child.tag, str):
            parts.append(etree.tostring(child, encoding="unicode", with_tail=True))
        else:
            parts.append(child.tail or "")
    return "".join(parts)


def parse_xml(text: str) -> list[XmlEntry]:
    """Parse decoded XML with DTD loading, networking and entity expansion disabled."""
    parser = etree.XMLParser(
        load_dtd=False,
        no_network=True,
        resolve_entities=False,
        recover=False,
        huge_tree=False,
        remove_comments=False,
    )
    normalized = re.sub(r"^\s*<\?xml[^>]*\?>", "", text, count=1)
    try:
        root = etree.fromstring(normalized.encode("utf-8"), parser=parser)
    except (etree.XMLSyntaxError, ValueError) as error:
        raise XmlParseError(f"invalid XML: {type(error).__name__}") from error

    namespace = (root.get("namespace") or "").strip()
    if not namespace:
        namespace = next(
            ((element.get("namespace") or "").strip() for element in root.iter()
             if isinstance(element.tag, str) and element.get("namespace")),
            "",
        )
    output: list[XmlEntry] = []
    seen: set[tuple[str, str]] = set()
    for element in root.iter():
        if not isinstance(element.tag, str):
            continue
        line = int(getattr(element, "sourceline", 0) or 0)
        element_text = _element_text(element).strip()
        statement_id = (element.get("id") or "").strip()
        skeleton = _SQL_COMMENTS_AND_STRINGS.sub(" ", element_text)
        if statement_id and element_text and _SQL_PATTERN.search(skeleton):
            key = f"{namespace}.{statement_id}" if namespace else statement_id
            identity = ("sql", key)
            if identity not in seen:
                seen.add(identity)
                end_line = line + element_text.count("\n") if line else 0
                output.append(XmlEntry(
                    key=key,
                    kind="sql",
                    text=element_text,
                    start_line=line,
                    end_line=end_line,
                    sql_type=_local_tag(element).lower(),
                ))
            continue

        hint = next(
            ((element.get(attribute) or "").strip() for attribute in _IDENTIFIER_ATTRS
             if _identifier((element.get(attribute) or "").strip())),
            "",
        )
        if not hint:
            hint = next(
                ((child.text or "").strip() for child in element
                 if "name" in _local_tag(child).lower() and _identifier((child.text or "").strip())),
                "",
            )
        if not hint:
            continue
        identity = ("artifact", hint)
        if identity in seen:
            continue
        seen.add(identity)
        block = etree.tostring(element, encoding="unicode", with_tail=False).strip()
        end_line = line + block.count("\n") if line else 0
        output.append(XmlEntry(
            key=hint,
            kind="artifact",
            text=block,
            start_line=line,
            end_line=end_line,
            related_fqns=tuple(sorted(set(_FQN.findall(block)))),
        ))
    return output
