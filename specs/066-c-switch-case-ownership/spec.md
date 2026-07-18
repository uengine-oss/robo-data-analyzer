# Spec 066: C Switch Case Ownership

## Problem

The parser emits C statements between `case` labels as direct `SWITCH`
children and can nest stacked labels. CASE nodes therefore cover only their
label lines, causing source-backed REFERENCES to be missing or assigned to the
wrong case.

## Requirements

- FR-001: Each C CASE range ends immediately before the next label, or at the
  enclosing switch end for the final label.
- FR-002: Stacked labels are sibling CASE controls under the SWITCH.
- FR-003: Routine ownership of calls and existing public graph schemas remain
  unchanged.
- FR-004: The normalization is deterministic and independent of project data.

## Acceptance

Focused range, parent, and call-owner tests pass; live control REFERENCES match
the source answer key with no missing or extra relation.
