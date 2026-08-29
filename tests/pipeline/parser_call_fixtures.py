from __future__ import annotations

from collections.abc import Iterable

from pipeline.models.analysis_context import CallOccurrenceFact


def parser_call_occurrences(
    calls: Iterable[tuple[int, str]],
) -> tuple[CallOccurrenceFact, ...]:
    """Build exact synthetic Parser call occurrences for focused unit tests."""
    return tuple(
        CallOccurrenceFact(
            call_id=f"test-call-{ordinal}",
            line=line,
            callee=name,
            call_source=f"{name}()",
            callee_source=name,
            source_range=(ordinal, 1),
        )
        for ordinal, (line, name) in enumerate(calls)
    )
