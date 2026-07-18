# Spec 065: C Function-Macro Call Classification

## Problem

ANTLR exposes invocations of function-like C macros as `FUNCTION_CALL` sites.
The call classifier currently turns any non-project, non-stdlib site into an
external function, creating false external stubs and `CALLS` relationships.

## Requirements

- FR-001: A function-like macro declared in the call site's C-family source
  file must not produce a `CALLS` relationship.
- FR-002: Detection must follow the C lexical distinction: `#define NAME(` is
  function-like, while `#define NAME (` is object-like.
- FR-003: Undeclared names must retain existing external-call behavior.
- FR-004: The result must expose a dropped-macro count for operations evidence.
- FR-005: Detection must be language- and project-neutral and perform no LLM or
  graph query.

## Acceptance

Focused positive and opposite-case tests pass, followed by the authoritative
Analyzer regression gates and a clean Framework live relation comparison.
