"""Shared bounded retry contract for independent semantic concerns."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable, Generic, Optional, TypeVar

from llm.usage.cost_budget import BudgetExceededError
from shared.events.runtime_logger import log_warning


T = TypeVar("T")
MAX_CONCERN_ATTEMPTS = 2


@dataclass(frozen=True)
class ConcernAttempt(Generic[T]):
    """One concern's immutable success or exhausted failure."""

    value: Optional[T]
    error: Optional[Exception]
    attempts: int

    @property
    def succeeded(self) -> bool:
        return self.error is None


async def run_concern_with_retry(
    *,
    component: str,
    concern: str,
    target_id: str,
    call: Callable[[], Awaitable[T]],
    max_attempts: int = MAX_CONCERN_ATTEMPTS,
) -> ConcernAttempt[T]:
    """Call one semantic producer with one bounded identical-request retry.

    The callable factory is invoked again only after that concern fails. Successful
    output is returned immediately and is never regenerated or merged.
    """
    if max_attempts < 1:
        raise ValueError("max_attempts must be positive")

    last_error: Optional[Exception] = None
    for attempt in range(1, max_attempts + 1):
        try:
            return ConcernAttempt(value=await call(), error=None, attempts=attempt)
        except BudgetExceededError:
            raise
        except Exception as error:  # noqa: BLE001 - typed failure is returned to the strategy
            last_error = error
            event = "concern_retry" if attempt < max_attempts else "concern_exhausted"
            log_warning(
                component,
                event,
                (
                    f"{concern} 조각 실패 — 동일 관심사만 재시도"
                    if attempt < max_attempts
                    else f"{concern} 조각 재시도 소진 — partial로 보존"
                ),
                concern=concern,
                target_id=target_id,
                attempt=attempt,
                max_attempts=max_attempts,
                error=f"{type(error).__name__}: {error}",
            )

    assert last_error is not None
    return ConcernAttempt(value=None, error=last_error, attempts=max_attempts)
