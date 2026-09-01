"""Test helpers for the family-owned semantic prompt tree."""
from __future__ import annotations

from pathlib import Path

from llm.runners.prompt_rules import _load_rule
from pipeline.meaning.prompt_assets import semantic_prompt_dir


ROOT = Path(__file__).resolve().parents[2]


def prompt_family(rule_name: str) -> str:
    if rule_name.startswith("code_summary"):
        return "summary"
    if rule_name.startswith("code_rule"):
        return "rule"
    if rule_name.startswith("code_table") or rule_name == "table_description":
        return "table"
    raise ValueError(f"unknown semantic rule: {rule_name}")


def prompt_dir(strategy: str, rule_name: str) -> Path:
    return semantic_prompt_dir(family=prompt_family(rule_name), strategy=strategy)


def load_semantic_rule(strategy: str, rule_name: str) -> dict:
    return _load_rule(prompt_dir(strategy, rule_name), rule_name)


def contract_path(family: str, name: str) -> Path:
    return ROOT / "pipeline" / "meaning" / family / "prompts" / "common" / name
