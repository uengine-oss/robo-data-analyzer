# Spec 067: CALLS Recursion Storage Contract

## Problem

The CALLS storage boundary silently discards unrecognized scopes. External
CALLS also lose the producer-confirmed `recursive` property in both Neo4j and
the live NDJSON stream.

## Requirements

- FR-001: The storage/stream split rejects every scope other than `internal`
  and `external`; an edge must never disappear silently.
- FR-002: External CALLS preserve `recursive` identically in Neo4j batch data,
  the Cypher relationship, and the live relationship event.
- FR-003: Internal self-loop validation remains fail-closed: identical
  endpoints require recursive intent, and recursive intent requires identical
  endpoints.
- FR-004: The contract is language- and project-neutral.

## Acceptance

Focused tests prove unknown-scope rejection and external recursive-property
round-tripping. The shopmall source recursive set and graph self-loop set remain
exactly equal with no accidental self-loop.
