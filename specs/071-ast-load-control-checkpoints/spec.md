# Spec 071: AST Load Control Checkpoints

## Problem

Run control is consumed at LangGraph step boundaries and inside LLM fan-outs,
but AST loading can parse, link, persist, and stream an entire project without
an internal control checkpoint. A stop requested during that step can therefore
remain pending beyond the client timeout.

## Requirements

- FR-001: The pause gate yields to the event loop so a concurrent control
  request can be observed at a cooperative checkpoint.
- FR-002: AST loading checks control between source files and between its
  deterministic linking, persistence, and stream batches.
- FR-003: Stop raises the existing `RunStopped` sentinel and reaches the normal
  `stopped` plus `stream_end` path; it is not reported as a pipeline failure.
- FR-004: Pause holds at a checkpoint and resume continues idempotent AST graph
  work without restarting completed files.
- FR-005: The implementation does not add project literals, polling, threads,
  or a second control state.

## Acceptance

Focused tests prove a stop after the first AST file prevents the second file
from being read and prove pause/resume continuation. Existing control, stream,
structure, full unit, prompt, compile, and live stop gates remain green.
