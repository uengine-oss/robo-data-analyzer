# C-086 call-effect source order and ordered arguments

**Date**: 2026-08-24

**Scope**: Analyzer only; sealed SKT one-file accuracy slice; no Neo4j write

## Cause

- C-085 retained occurrence arguments and ranges, but `direct_call_occurrences`
  sorted same-line calls by hash ID and the final materializer still emitted only
  `call("target")`.
- The hash order is not source order, and the target-only leaf made different
  argument occurrences look identical.

## Decision

- Occurrence call effects are ordered by routine line, exact call-range offset and
  canonical ID tie-breaker.
- Their closed Python leaf is
  `call("target", F("receiver") | None, [F("argument0"), ...])`.
- The argument list is Parser grammar order. The receiver and arguments are exact
  source fragments; Analyzer does not parse or reinterpret them.
- This is source order, not a fabricated language runtime evaluation order.
- Legacy/DBMS effects without occurrence metadata keep `call("target")`.

## Sealed actual

| Item | Result |
|---|---:|
| source SHA-256 | `edd512c6478cb31f7a0f396f92ec850659f2720d25c553b8a4f4e42ba330d126` |
| Parser artifact SHA-256 | `94c5bbe98bfbf51b6dabc564c73361c417fb53d32b4308689a8988ab68b6a831` |
| call effect leaves | 974 |
| expanded occurrence leaves | 974 / 974 |
| Python parse | 974 / 974 |
| exact source order ledger | 974 / 974 |
| Rule-to-Example equality | 974 / 974 |
| focused regression | 168 passed + 20 subtests |
| staged-only full regression | 1,301 passed / 8 skipped / 367 subtests |

At Rule `R232`, four `pfm_try` occurrences now retain respectively
`a000_init_proc(ctx)`, `b000_input_validation(ctx)`, `c000_main_proc(ctx)` and
`z000_norm_exit_proc(ctx)` as ordered argument fragments. Nested calls remain their
own canonical occurrences; the leaf order claims source order only.

No full 23-file run was performed, and this structural result is not promoted to
LLM business-meaning accuracy or product acceptance.
