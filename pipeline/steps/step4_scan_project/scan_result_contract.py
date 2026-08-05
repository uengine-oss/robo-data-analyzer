"""Scan Agent output boundary shared by every framework consumer."""
from __future__ import annotations

from agents.contracts import ScanResult


class ScanResultContractError(ValueError):
    """The framework scan output is missing or violates its closed schema."""


def require_scan_result(state: dict) -> ScanResult:
    """Return one validated scan contract; never turn invalid state into empty facts."""
    if "scan_result" not in state:
        raise ScanResultContractError("scan_result is missing from workflow state")
    raw = state["scan_result"]
    if isinstance(raw, ScanResult):
        return raw
    try:
        return ScanResult.model_validate(raw)
    except Exception as error:
        raise ScanResultContractError(
            f"scan_result violates ScanResult: {type(error).__name__}: {error}"
        ) from error
