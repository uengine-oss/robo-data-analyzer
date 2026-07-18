# Spec 069: Control Subtree Context Contract

## Problem

Framework control nodes are persisted with correct `PARENT_OF` edges, but no
production code converts that tree into the `control_subtree` consumed by the
rules fallback. An anomalous rules result therefore cannot enter the intended
deterministic fragment-analysis path.

## Requirements

- FR-001: Framework routine contexts receive their complete IF/ELSE/LOOP/
  SWITCH/CASE/TRY/CATCH subtree without a graph query.
- FR-002: Subtree coordinates are 1-based relative to the routine `code_text`,
  while persisted node coordinates remain unchanged.
- FR-003: Parent/child nesting and source order are deterministic.
- FR-004: Structural nodes remain excluded from Framework LLM analysis and do
  not receive a subtree as if they were routines.
- FR-005: The implementation derives control labels from the language ontology
  and introduces no project-specific data or reverse step6-to-step2 import.

## Acceptance

Focused tests prove relative coordinates, nesting, context wiring, and an
actual anomalous fallback producing at least one fragment. Authoritative
Analyzer gates and prompt byte identity remain green.
