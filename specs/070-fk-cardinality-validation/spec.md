# Spec 070: FK Cardinality and Validation Contract

## Problem

DDL parsing emits FK edges with dataclass defaults, while the integration validator
compares only table endpoints. A graph can therefore pass even when a foreign
key is attached to the wrong column or carries an incorrect `fk_type` or
`is_candidate` value.

## Requirements

- FR-001: A declared child-to-parent FK is `one_to_one` exactly when its full
  local column set is a declared local unique key (primary key or UNIQUE key).
- FR-002: All other declared child-to-parent FKs are `many_to_one`; a subset of
  a composite unique key is not unique.
- FR-003: Composite FK members share the cardinality of their full local key,
  and declared DDL FKs have `is_candidate=false`.
- FR-004: Validation identifies an FK by both table and column endpoints and
  fails on missing, extra, or property-mismatched edges.
- FR-005: Expected FK properties are derived from source/answer-key data and
  are not copied from the product graph.

## Acceptance

Focused tests cover ordinary, unique, composite-unique, and composite-subset
FKs plus validator property mismatch detection. Authoritative Analyzer and
validator gates remain green, and a live source-to-graph comparison reports no
FK identity or property differences.
