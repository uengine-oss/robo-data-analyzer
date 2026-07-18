# Feature Specification: Catalog Lineage Route Contract

**Created**: 2026-07-18
**Status**: Complete

## Problem

The Catalog public route is `GET /robo/lineage/`, while the Analyzer calls
`/robo/lineage`. Catalog redirects the latter, and the fail-fast Analyzer HTTP
client intentionally does not follow redirects. A DBMS run therefore reaches
the final integration step and fails despite both services being healthy.

## Requirements

- Analyzer constructs the documented Catalog lineage URL with its trailing
  slash.
- Redirect following remains disabled; contract drift must stay visible.
- No Catalog error is converted into a success.

## Acceptance

An exact URL unit contract passes, both service endpoints pass live preflight,
and the authoritative Analyzer gates remain green.
