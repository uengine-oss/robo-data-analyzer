"""Evidence-reference safety for the human-facing Rule narrative.

``C12`` and ``E7`` are private prompt coordinates, but the same spellings can also be
real legacy status codes.  A narrative is therefore considered leaked only when it
repeats at least two identifiers that were actually exposed by that row.  Detection
and deterministic substitution share this module so the producer and run judge cannot
drift.
"""
from __future__ import annotations

import re
from collections.abc import Callable, Mapping


REFERENCE_ID = re.compile(r"(?<![A-Za-z0-9_])[CE][1-9][0-9]*(?![A-Za-z0-9_])")
MIN_REFERENCE_LEAK = 2


def exposed_reference_ids(row: Mapping[str, object]) -> frozenset[str]:
    """Return the private condition/effect IDs supplied for one evidence row."""
    return frozenset(
        str(value).lstrip("!")
        for value in tuple(row.get("when") or ()) + tuple(row.get("direct") or ())
    )


def leaked_reference_ids(narrative: str, row: Mapping[str, object]) -> tuple[str, ...]:
    """Return exact exposed IDs repeated in ``narrative``, preserving occurrences."""
    exposed = exposed_reference_ids(row)
    return tuple(value for value in REFERENCE_ID.findall(narrative) if value in exposed)


def reference_leak_count(narrative: str, row: Mapping[str, object]) -> int:
    return len(leaked_reference_ids(narrative, row))


def replace_leaked_references(
    narrative: str,
    row: Mapping[str, object],
    replacement: Callable[[str], str],
) -> str:
    """Substitute only a proven multi-reference leak with grounded source text.

    A single token remains untouched because it can be a legitimate business code.
    The caller owns rendering the referenced parser evidence; this function owns only
    exact-token recognition and the ambiguity threshold.
    """
    leaked = leaked_reference_ids(narrative, row)
    if len(leaked) < MIN_REFERENCE_LEAK:
        return narrative
    leaked_set = frozenset(leaked)
    return REFERENCE_ID.sub(
        lambda match: replacement(match.group(0))
        if match.group(0) in leaked_set else match.group(0),
        narrative,
    )
