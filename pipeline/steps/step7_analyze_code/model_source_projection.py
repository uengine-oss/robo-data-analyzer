"""Lossless model-facing projection for quote-delimited source literals.

The canonical source file and deterministic parser evidence remain untouched.  Only text
placed in a natural-language JSON generation prompt is projected so provider tool parsers
cannot confuse a copied source delimiter/escape with the response JSON boundary.
"""
from __future__ import annotations


_ESCAPE_NAMES = {
    "0": "NUL",
    "t": "TAB",
    "r": "CARRIAGE_RETURN",
    "n": "NEWLINE",
    "b": "BACKSPACE",
    "f": "FORM_FEED",
    "v": "VERTICAL_TAB",
    "\\": "BACKSLASH",
    '"': "DOUBLE_QUOTE",
    "'": "SINGLE_QUOTE",
}


def _literal_content(text: str) -> str:
    out: list[str] = []
    index = 0
    while index < len(text):
        char = text[index]
        if char == "\\" and index + 1 < len(text):
            escaped = text[index + 1]
            out.append(f"<CHARACTER {_ESCAPE_NAMES.get(escaped, 'ESCAPE_' + escaped)}>" )
            index += 2
            continue
        if char == "`":
            out.append("<CHARACTER BACKTICK>")
        elif ord(char) < 32 or ord(char) == 127:
            out.append(f"<CHARACTER U+{ord(char):04X}>")
        else:
            out.append(char)
        index += 1
    return "".join(out)


def project_model_source_literals(text: str) -> str:
    """Replace quote delimiters while preserving literal content and line coordinates.

    Both C/Java/Python-style backslash escapes and SQL doubled quote delimiters are
    recognized.  Newlines are never removed or inserted, so absolute source line prefixes
    and parser coordinates stay valid.
    """
    out: list[str] = []
    index = 0
    while index < len(text):
        quote = text[index]
        if quote not in {"'", '"'}:
            out.append(quote)
            index += 1
            continue

        cursor = index + 1
        content: list[str] = []
        closed = False
        while cursor < len(text) and text[cursor] not in "\r\n":
            char = text[cursor]
            if char == "\\" and cursor + 1 < len(text):
                content.extend((char, text[cursor + 1]))
                cursor += 2
                continue
            if char == quote:
                # SQL escapes a delimiter by doubling it inside the literal.
                if cursor + 1 < len(text) and text[cursor + 1] == quote:
                    content.extend((quote, quote))
                    cursor += 2
                    continue
                closed = True
                cursor += 1
                break
            content.append(char)
            cursor += 1

        if not closed:
            # An unmatched apostrophe in a comment is prose, not a source literal.
            out.append(quote)
            index += 1
            continue
        out.append("[QUOTED_LITERAL value=")
        out.append(_literal_content("".join(content)))
        out.append("]")
        index = cursor
    return "".join(out)
