"""Gold corpus loader and self-consistency checks (spec 078, S1/T104).

Gold contract: for every fixture ``fixtures/<fmt>/<stem>.<ext>`` there is an
``expected/<stem>.json`` whose ``chunks[*].text`` each occur exactly once in
the decoded fixture source, in order and without overlap.  Adapter tests (S2)
reuse ``iter_gold``/``locate_spans`` for boundary scoring.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path

GOLD_DIR = Path(__file__).resolve().parent
FIXTURES_DIR = GOLD_DIR / "fixtures"
EXPECTED_DIR = GOLD_DIR / "expected"


@dataclass(frozen=True)
class GoldCase:
    stem: str
    format: str
    fixture_path: Path
    source: str
    fail_closed: bool
    chunks: tuple[dict, ...]


def decode_fixture(path: Path) -> str:
    raw = path.read_bytes()
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("cp949")


def iter_gold() -> list[GoldCase]:
    cases: list[GoldCase] = []
    for fixture in sorted(FIXTURES_DIR.rglob("*")):
        if not fixture.is_file():
            continue
        expected_path = EXPECTED_DIR / f"{fixture.stem}.json"
        if not expected_path.is_file():
            raise AssertionError(f"gold fixture without expected file: {fixture.name}")
        expected = json.loads(expected_path.read_text(encoding="utf-8"))
        cases.append(GoldCase(
            stem=fixture.stem,
            format=expected["format"],
            fixture_path=fixture,
            source=decode_fixture(fixture),
            fail_closed=bool(expected["fail_closed"]),
            chunks=tuple(expected["chunks"]),
        ))
    if not cases:
        raise AssertionError("gold corpus is empty")
    return cases


def locate_spans(source: str, chunk_texts: list[str]) -> list[tuple[int, int]]:
    """Map each expected chunk text to its unique (start, end) span, in order."""
    spans: list[tuple[int, int]] = []
    for text in chunk_texts:
        if not text:
            raise AssertionError("gold chunk text must be non-empty")
        first = source.find(text)
        if first < 0:
            raise AssertionError(f"gold chunk text not found in source: {text[:60]!r}")
        if source.find(text, first + 1) >= 0:
            raise AssertionError(f"gold chunk text is not unique in source: {text[:60]!r}")
        spans.append((first, first + len(text)))
    ordered = sorted(spans)
    if spans != ordered:
        raise AssertionError("gold chunks are not in source order")
    for (_, prev_end), (next_start, _) in zip(spans, spans[1:]):
        if next_start < prev_end:
            raise AssertionError("gold chunk spans overlap")
    return spans
