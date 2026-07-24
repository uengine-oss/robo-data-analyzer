"""활성 코드와 비활성 텍스트 분리 — 언어 지식은 ``source_lexicon`` 표에서만 온다.

"근거"의 정의가 여기서 결정된다. 감사(docs 05)가 확정한 결함 중 하나가 **주석 처리된 죽은
코드의 필드를 실행 사실로 서술**한 것이라, 주석·비활성 블록은 활성 코드와 반드시 갈라야 한다.
문자열 리터럴 안의 ``//`` 를 주석으로 오인하면 반대 방향 오탐이 나므로 인용 구간을 보호한다.

줄 정렬을 보존하는 이유: ``control_subtree`` 가 줄번호로 분기 범위를 주므로(spec 069) 분리
뒤에도 같은 좌표계로 잘라 쓸 수 있어야 한다.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from pipeline.languages import source_lexicon

_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_NUM = re.compile(r"\d+")


@dataclass(frozen=True)
class SplitSource:
    """줄 단위로 정렬된 활성/비활성 텍스트. 두 배열의 길이는 원본 줄 수와 같다."""

    live_lines: tuple[str, ...]
    dead_lines: tuple[str, ...]

    def live(self, start: int = 1, end: int | None = None) -> str:
        """1-based 줄 범위의 활성 텍스트."""
        stop = len(self.live_lines) if end is None else end
        return "\n".join(self.live_lines[max(0, start - 1) : stop])

    def dead(self, start: int = 1, end: int | None = None) -> str:
        stop = len(self.dead_lines) if end is None else end
        return "\n".join(self.dead_lines[max(0, start - 1) : stop])


def split_source(text: str, lang: str) -> SplitSource:
    """주석·비활성 블록을 걷어내고 줄 정렬을 보존한 채 활성/비활성으로 가른다."""
    lexicon = source_lexicon(lang)
    line_tokens: list[str] = list(lexicon.get("line") or [])
    block_pairs: list[tuple[str, str]] = [tuple(pair) for pair in (lexicon.get("block") or [])]
    quotes: set[str] = set(lexicon.get("string_quotes") or [])
    inactive: list[str] = list(lexicon.get("inactive") or [])

    live_lines: list[str] = []
    dead_lines: list[str] = []
    open_block: tuple[str, str] | None = None

    for raw in text.splitlines():
        live: list[str] = []
        dead: list[str] = []
        index, length, quote = 0, len(raw), ""
        while index < length:
            if open_block is not None:
                close = open_block[1]
                found = raw.find(close, index)
                if found < 0:
                    dead.append(raw[index:])
                    index = length
                else:
                    dead.append(raw[index:found])
                    live.append(" ")
                    index = found + len(close)
                    open_block = None
                continue
            if quote:
                live.append(raw[index])
                if raw[index] == "\\" and index + 1 < length:
                    live.append(raw[index + 1])
                    index += 2
                    continue
                if raw[index] == quote:
                    quote = ""
                index += 1
                continue
            token = next((t for t in line_tokens if raw.startswith(t, index)), None)
            if token is not None:
                dead.append(raw[index:])
                index = length
                continue
            pair = next((p for p in block_pairs if raw.startswith(p[0], index)), None)
            if pair is not None:
                open_block = pair
                index += len(pair[0])
                continue
            if raw[index] in quotes:
                quote = raw[index]
                live.append(raw[index])
                index += 1
                continue
            live.append(raw[index])
            index += 1
        live_lines.append("".join(live))
        dead_lines.append("".join(dead))

    if inactive:
        live_lines, dead_lines = _mask_inactive(live_lines, dead_lines, inactive)
    return SplitSource(tuple(live_lines), tuple(dead_lines))


def _mask_inactive(
    live: list[str], dead: list[str], inactive: list[str]
) -> tuple[list[str], list[str]]:
    """전처리기 비활성 블록(예: C 의 ``#if 0``)을 활성에서 비활성으로 옮긴다."""
    open_re, nest_re, close_re = (re.compile(pattern) for pattern in inactive)
    depth = 0
    for index, line in enumerate(live):
        stripped = line.strip()
        if depth:
            if nest_re.match(stripped):
                depth += 1
            elif close_re.match(stripped):
                depth -= 1
            dead[index] = f"{dead[index]} {line}".strip()
            live[index] = ""
            continue
        if open_re.match(stripped):
            depth = 1
            dead[index] = f"{dead[index]} {line}".strip()
            live[index] = ""
    return live, dead


def literal_values(text: str, lang: str) -> set[str]:
    """소스의 **코드값 리터럴** — 인용된 값과 수치. 식별자는 뺀다.

    산출물 쪽에서 대조 가능한 것이 도메인 코드값뿐이라(프롬프트가 변수·메서드 토큰을 금지),
    값 대조축은 이 집합이어야 한다. 식별자를 섞으면 변수명이 "조건값"으로 오인된다.
    """
    found = {match.group(0) for match in _NUM.finditer(text)}
    for quote in source_lexicon(lang).get("string_quotes") or []:
        escaped = re.escape(quote)
        for match in re.finditer(rf"{escaped}([^{escaped}\\\n]{{0,64}}){escaped}", text):
            value = match.group(1).strip()
            if value:
                found.add(value.lower())
    return found


def atoms(text: str, lang: str) -> set[str]:
    """소스의 대조 원자 — 식별자 + 코드값 리터럴(소문자 정규화)."""
    return {m.group(0).lower() for m in _IDENT.finditer(text)} | literal_values(text, lang)
