"""Narrow retry policy for failures that produced no usable model meaning."""
from __future__ import annotations


MAX_TRANSPORT_ATTEMPTS = 3
TRANSPORT_RETRY_DELAYS = (0.5, 1.5)

_TRANSIENT_NAMES = frozenset({
    "ConnectError",
    "ConnectTimeout",
    "ReadError",
    "ReadTimeout",
    "RemoteProtocolError",
    "PoolTimeout",
    "RateLimitError",
    "ServiceUnavailableError",
    "InternalServerError",
})


def is_transient_transport_error(error: BaseException) -> bool:
    """Retry only transport, rate-limit, and server failures."""
    if type(error).__name__ in _TRANSIENT_NAMES:
        return True
    status = getattr(error, "status_code", None)
    if status is None:
        status = getattr(getattr(error, "response", None), "status_code", None)
    return isinstance(status, int) and (status == 408 or status == 429 or status >= 500)
