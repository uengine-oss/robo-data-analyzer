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
in `pipeline/strategies.py`. `pipeline/execution/langgraph_builder.py` assembles the LangGraph
StateGraph with **no strategy `if` branches**. A step tuple = a parallel superstep group.
Adding a new strategy MUST touch exactly one place (the declaration) and zero control-flow
branches.

**Rationale**: branching logic scattered across steps is where divergence bugs breed.

### III. Single Source of Truth (단일 진실)

Each cross-cutting fact has exactly one home: all `ROBO_*` environment config lives in
`shared/config/settings.py` (the `SETTINGS` singleton); all Neo4j relationship names live in
`graph/schema/rel_types.py` (never a Cypher string literal). No magic numbers inline.

**Rationale**: one definition, one place to change, no drift.

### IV. No Silent Failure (silent fail 금지)

Every **operational or deterministic-contract failure** that makes the phase unable to
produce a trustworthy result MUST `log_error` + `stream.emit(error)` + `raise`. Returning a
quietly empty result is forbidden (the unresolved-queue-emptied incident). Absent inputs that
invalidate a phase (e.g. missing antlr AST dir), exhausted transport, missing audit evidence,
budget overflow, and deterministic invariant violations are fatal.

An LLM candidate that fails schema, syntax, grounding, or semantic validation is a
**quality finding, not an operational step failure**. The producer MUST preserve the exact
target coordinates, candidate/evidence reference, and reasons; quarantine the unverified
candidate from trusted downstream derivation; emit the finding to logs and run results; and
continue independent work. It MUST NOT fabricate an empty success, silently drop the finding,
retry content, or turn one bad candidate into a whole-run failure. Run completion MUST expose
that findings exist.

**Rationale**: both extremes corrupt truth. A silent empty result looks like success, while
aborting a full rebuild for one model-quality defect destroys the evidence needed to measure
and improve that defect and can make reanalysis impossible.

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

### VII. Structured Output Channels (구조화 출력 채널)

**Agent** structured output MUST be forced via a `submit_*` tool wrap (`structured_agent.py`);
agents finalize by calling their submit tool exactly once. **Simple (non-agent) calls** use
`llm.with_structured_output(..., include_raw=True)`. The provider/deployment config owns the
default channel, and an output-contract YAML MAY override it only with recorded wire and
failure evidence. The resolved channel is part of the response-cache key. Provider adapters
MUST receive a deep copy of the canonical schema because strict tool conversion may mutate it.
Both channels fail closed through the same internal Pydantic validation and raw/usage evidence.

**Rationale**: the 2026-08-03 probe sanctioned `response_format json_schema strict` after
grammar masking worked and the historical hang did not reproduce. A fresh production prompt
on 2026-08-04 re-detected a narrower xgrammar deadlock: a quote intended as string content was
accepted as the JSON closing quote, after which the model wanted literal whitespace while the
grammar wanted a comma, so only structural whitespace repeated. The identical prompt/schema
completed through strict function calling, but a real 24-row RULE tool schema was rejected as
`Invalid grammar request`; therefore neither channel is universally correct. Spec 116 TA-58~62
records the bounded probes and permits a rule-level override only for a schema shape actually
proven on the current deployment. Schema-validation failures and repetition aborts MUST retain
their raw/partial input and MUST NOT trigger content retries.

### VIII. Explicit Ownership and Dependency Direction (명시적 소유권과 의존 방향)

Every module MUST live under the owner that changes for the same reason: pipeline, graph, LLM,
search, logging, API, or system configuration. High-level orchestration may depend on these
owners; low-level contracts MUST NOT import a heavyweight orchestrator merely to reuse a small
helper. Shared behavior belongs in the narrowest stable owner with all consumers identified.

**Rationale**: explicit ownership prevents generic shared dumping grounds, circular dependencies,
duplicate truths, and refactors that only make the directory tree cosmetically smaller.

### IX. Three-Layer Verification (3층 검증 의무)

An analysis result is "verified" only when **all three layers** have run and been reported.
No layer substitutes for another.

1. **Automated tests** — unit/contract suites and prompt byte identity.
2. **Deterministic answer-key comparison** — the answer already lives in the request. Row
   obligation accounting (`rows = trusted + findings + unavailable`, zero residual), CALL /
   R-W / RETURN occurrence keys, Rule↔branch-table subtraction (refs must exist in *that*
   request's evidence), and EXAMPLE derivation equivalence.
3. **Direct semantic adjudication by the main agent** — a pre-frozen stratified sample of
   code nodes plus **every** table, judging information sufficiency first, then meaning.
   A separate LLM judge is forbidden: a verifier that shares the model's limits is not a verifier.

Any axis not measured MUST be reported as `미검증`, never folded into a pass. Passing tests
is not evidence of semantic accuracy. Procedure detail lives in
`docs/handovers/analysis-performance-validation-canonical/04-검증방법.md`, which this
principle makes binding rather than advisory.

**Rationale**: on 2026-08-07 a prompt overhaul was declared "all axes passed" after only
layer 1 and part of layer 2 had run; four answer-key comparisons and the entire semantic
layer were never executed. The procedure existed and was simply not read, because nothing
required it.

### X. Root-Cause Order (원인 추적 순서)

When a result looks wrong, start from **one wrong coordinate**, never from an aggregate
statistic, and walk the production chain **front to back**:

`source → parser AST → deterministic assembly → the prompt actually sent → information
sufficiency → grader/answer-key → model & prompt`

The model is the **last** suspect. Information sufficiency is judged before blame: if the
main agent cannot determine the answer from what the model received, that is `needs_context`
and the defect is upstream (evidence production), not the model. Failures are grouped by
common cause and fixed **generically once**, then re-checked with a fixed replay; sample-specific
answers MUST NOT be hardcoded.

**Rationale**: measured both ways in one session. A trigger-header fatal was traced to
deterministic assembly (statement dml promoted to table op) — the model was never involved.
The nl reference-number leak was traced to the prompt contract defining `nl` as a restatement
of fields the model had just filled. Starting at the statistic would have produced a wording
patch for both.

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

**Version**: 1.4.0 | **Ratified**: 2026-06-15 | **Last Amended**: 2026-08-07
(1.4.0 — IX and X added: three-layer verification made binding and root-cause order fixed,
after a session declared a prompt overhaul "passed" with two of three layers unrun. 1.3.2 —
IV amended to distinguish fatal operational/deterministic-contract failures from surfaced,
quarantined LLM quality findings so a bad candidate cannot masquerade as success or abort an
otherwise auditable rebuild; VII remains as amended in 1.3.1; see spec 116 TA-77~80)
