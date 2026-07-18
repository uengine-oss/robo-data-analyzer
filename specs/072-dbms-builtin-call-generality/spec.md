# Feature Specification: DBMS Built-in Call Generality

**Created**: 2026-07-18
**Status**: Complete

## Problem

The DBMS call linker drops every callee whose text begins with an Oracle-like
prefix. That can silently discard application routines such as `DBMS_CUSTOM`
or `SYS_TIME`, and the policy is embedded in Python rather than declared as
supported-platform data. A stale pipeline comment also lacks an executable
proof that the Framework deterministic linker is absent from DBMS runs.

## Requirements

- A project-defined routine always resolves as internal before any built-in
  classification.
- Built-ins are recognized only by an exact qualified package name from a
  data-owned, dialect-labelled registry; name prefixes are never sufficient.
- Unknown qualified and unqualified callees remain visible as external calls.
- The DBMS strategy contains `link_dbms_tables_step` and does not contain
  `link_deterministic_step`.
- The policy is project-neutral and does not contain RWIS-specific symbols.

## Acceptance

Focused tests cover project-prefix collisions, exact built-in package calls,
unknown lookalikes, and the DBMS strategy step tuple. Authoritative structure,
full, prompt-hash, compile, and diff gates pass before live RWIS execution.
