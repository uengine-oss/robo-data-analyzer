"""Resolve family-owned prompt assets without exposing paths to the LLM layer."""
from __future__ import annotations

from pathlib import Path


_ROOT = Path(__file__).resolve().parent
_FAMILIES = frozenset({"summary", "table", "rule"})
_STRATEGIES = frozenset({"framework", "dbms"})


def semantic_prompt_dir(*, family: str, strategy: str) -> Path:
    if family not in _FAMILIES:
        raise ValueError(f"unknown semantic prompt family: {family}")
    if strategy not in _STRATEGIES:
        raise ValueError(f"unknown semantic prompt strategy: {strategy}")
    path = _ROOT / family / "prompts" / strategy
    if not path.is_dir():
        raise FileNotFoundError(f"semantic prompt directory is missing: {path}")
    return path
