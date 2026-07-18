# ROBO Data Analyzer Constitution

<!--
  These principles describe what the robo-data-analyzer codebase ALREADY embodies.
  They were derived (2026-06-15) by reverse-engineering the implementation, not from a
  prior design doc. Every new spec/plan MUST be checked against them ("Constitution Check"
  gate in plan.md). Amending a principle requires bumping the version below and noting why.
-->

## Core Principles

### I. Determinism-First (결정론 우선)

LLM calls are reserved for steps that genuinely require *meaning*. Everything that can be
computed deterministically MUST be: AST/DDL parsing, code↔table R/W grep linking, call
classification, the bottom-up analysis DAG (Tarjan SCC + Kahn rounds), the Example Mapping
builder, Leiden component clustering, and health metrics are all **LLM 0**.

**Rationale**: determinism makes runs reproducible, cheap, and auditable. An LLM where a
regex or graph algorithm suffices is a defect, not a feature.

### II. Zero-Branch Strategy Pattern (분기 0)

Pipeline variants (`framework` / `dbms`) are expressed by **declaring** a `Strategy(steps=…)`
in `pipeline/analysis_pipelines.py`. `pipeline/execution/langgraph_builder.py` assembles the LangGraph StateGraph with **no
strategy `if` branches**. A step tuple = a parallel superstep group. Adding a new strategy
MUST touch exactly one place (the declaration) and zero control-flow branches.

**Rationale**: branching logic scattered across steps is where divergence bugs breed.

### III. Single Source of Truth (단일 진실)

Each cross-cutting fact has exactly one home: all `ROBO_*` environment config lives in
`analyzer_settings.py` (the `SETTINGS` singleton); all Neo4j relationship names live in
`graph/schema/rel_types.py` (never a Cypher string literal). No magic numbers inline.

**Rationale**: one definition, one place to change, no drift.

### IV. No Silent Failure (silent fail 금지)

Every step failure MUST `log_error` + `stream.emit(error)` + `raise`. Returning a quietly
empty result is forbidden (the unresolved-queue-emptied incident). Absent inputs that
invalidate a phase (e.g. missing antlr AST dir) MUST raise an explicit error, not skip.

**Rationale**: a silent empty result looks like success and corrupts the graph downstream.

### V. KV-Cache-Friendly Prompts (KV 캐시 친화)

LLM prompt system bodies that share a prefix MUST be **byte-identical** so the inference
cache (KV/RadixAttention) hits. The 3-split fan-out shares one `llm/prompts/framework/_common.yaml`
via `extends`. Prompt files are SHA256-pinned in `llm/prompts/prompt_hashes.json` and guarded by CI
(`byte_identical.yml`) — any drift fails the build.

**Rationale**: a one-character diff in a shared system prompt silently destroys cache reuse
and inflates cost/latency.

### VI. Schema Restraint on LLM Output (출력 스키마 절제)

Forced output schemas MUST stay minimal. Free-form natural-language fields (e.g. Example
Mapping `given/when/then`) are single strings, NOT nested/repeated structures — over-constrained
schemas caused repetition/whitespace blowups (2026-05-30 incident).

**Rationale**: the schema is a guardrail, not a straitjacket; over-specification backfires.

### VII. Submit-Tool Wrap for Agent Output (submit 도구 패턴)

Structured agent output MUST be forced via a `submit_*` tool wrap (`structured_agent.py`),
NOT by passing `response_format` directly — the latter hangs under qwen + SGLang xgrammar
masking. Agents finalize by calling their submit tool exactly once.

**Rationale**: provider-specific grammar masking deadlocks; the submit-tool pattern is the
portable way to guarantee a typed result.

### VIII. Explicit Ownership and Dependency Direction (명시적 소유권과 의존 방향)

Every module MUST live under the owner that changes for the same reason: pipeline, graph, LLM,
search, logging, API, or system configuration. High-level orchestration may depend on these
owners; low-level contracts MUST NOT import a heavyweight orchestrator merely to reuse a small
helper. Shared behavior belongs in the narrowest stable owner with all consumers identified.

**Rationale**: explicit ownership prevents generic shared dumping grounds, circular dependencies,
duplicate truths, and refactors that only make the directory tree cosmetically smaller.

## Cross-Service Contracts

The analyzer is one service in a pipeline. Its boundaries (and therefore the only things that
ripple to other services when changed) are:

- **↔ Catalog service**: HTTP `sample-context` and `/lineage` endpoints.

A spec change that does NOT touch a contract is internal (no cross-service impact). A spec
change that touches a contract MUST flag the affected downstream service.

## Cost & Safety Constraints

- A per-run USD circuit breaker (`ROBO_BUDGET_RUN_USD`, default 20) MUST abort a run that
  exceeds budget (the "$400 incident" prevention).
- Each run is destructive by policy: it wipes the Neo4j graph (`DETACH DELETE`) and rmtrees
  `logs/` at start. Specs MUST treat a run as a full rebuild, not an incremental update.
- The model is single-concurrent-run; pause/resume/stop is an in-process soft hold with no
  disk resume.

## Governance

This constitution supersedes ad-hoc convention. New features go through
`/speckit-specify → /speckit-plan`; each `plan.md` MUST include a Constitution Check section
verifying compliance with the principles above. A principle is amended only by editing this
file and bumping the version, with the rationale recorded.

**Version**: 1.2.0 | **Ratified**: 2026-06-15 | **Last Amended**: 2026-07-16
