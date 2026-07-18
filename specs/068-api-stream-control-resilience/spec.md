# Spec 068: API, Stream, and Control Resilience

## Problem

The public search request accepts `top_n` but does not apply it. A step7
exception prevents all checkpointed step1-6 state from being written to run
artifacts. Four step7 LLM fan-outs are not registered with the active pause
controller, so stop/pause cannot cancel them promptly.

## Requirements

- FR-001: REST search validates `top_n` at the request boundary and passes it
  through the shared REST/MCP search service without mutating global defaults.
- FR-002: A pipeline exception preserves the latest checkpointed state in the
  normal artifact files; artifact-write failure must not replace the original
  pipeline error.
- FR-003: Every step7 item fan-out runs through the active pause controller,
  cancels and collects in-flight work on stop, and reconstructs work after
  pause/resume.
- FR-004: NDJSON producers are collected when consumers detach, and graph
  relationship events remain fail-closed and storage-isomorphic.
- FR-005: API/MCP success, failure, and boundary responses remain explicit;
  `node_detail` never omits source line availability metadata.

## Acceptance

Focused contract tests pass; live REST/MCP responses and stop/pause/resume
events are captured; failure-run artifacts contain pre-failure checkpointed
state; persisted graph and stream relation sets have no missing or extra edge.
