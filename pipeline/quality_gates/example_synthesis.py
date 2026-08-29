"""Exact Rule-to-Example projection with no second condition interpretation."""
from __future__ import annotations


def synthesize_examples(rules) -> dict[int, list[dict]]:
    """Reuse each verified Rule condition and effects byte-for-byte exactly once.

    Analyzer already resolved branch polarity, nesting, and complements before Rule
    materialization. Parsing or simplifying the Python condition here would create a
    second meaning path and can change spelling, grouping, or literals. An
    unconditional Rule has no demonstrable given-state scenario, and a Rule without a
    verified business effect has no product Example.
    """
    synthesized: dict[int, list[dict]] = {}
    for rule in rules or ():
        anchor = rule.anchor_line
        condition = (rule.structural_condition or "").strip()
        effects = [str(effect) for effect in (rule.business_effects or ())]
        synthesized[anchor] = (
            [{
                "given_conditions": [condition],
                "expected_effects": effects,
            }]
            if condition and effects
            else []
        )
    return synthesized
