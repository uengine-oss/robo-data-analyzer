# Implementation Plan: Semantic Search, Handoff, and Table-Effect Provenance

**Branch**: `main` | **Date**: 2026-07-30 | **Spec**: [spec.md](spec.md)

## Summary

Unify all semantic retrieval scoring on `search_text`, route callee `search_text` through the
Framework bottom-up CALLS handoff, stop repetition-loop retries, and add deterministic table
effect provenance without expanding the LLM-owned schema. Persist the new AFFECTS_TABLE contract
and update Architect consumers in its paired spec 050.

## Technical Context

**Language/Version**: Python 3.11+

**Primary Dependencies**: Pydantic v2, LangChain, Neo4j, rank-bm25, sentence-transformers

**Storage**: Neo4j Analyzer graph

**Testing**: unittest discovery for Analyzer; targeted pytest for Architect consumer

**Target Platform**: Windows development/runtime, Python services

**Project Type**: multi-service analysis and architecture application

**Performance Goals**: no added LLM call; no added CALLS scheduling barrier; repetition abort
cost is one request rather than two

**Constraints**: preserve spec 106 scanner WIP; maintain prompt KV-prefix identity; no silent
fallback from missing search text to summary; no false exact DML

**Scale/Scope**: all analyzed code nodes and all AFFECTS_TABLE consumers

## Constitution Check

- **I Determinism-First — PASS**: access and provenance are finalized without an LLM call.
- **II Zero-Branch Strategy — PASS**: Framework/DBMS strategy declarations are unchanged.
- **III Single Source of Truth — PASS**: one semantic scoring field and one effect finalizer.
- **IV No Silent Failure — PASS**: missing search text excludes a semantic node; repetition
  abort is returned explicitly as a failed concern.
- **V KV-Cache-Friendly Prompts — PASS**: only the shared Framework common prompt changes and
  pinned hashes are regenerated after byte-identity verification.
- **VI Schema Restraint — PASS**: provenance does not expand the LLM response fields; op becomes
  optional instead of more constrained.
- **VII Submit Tool — N/A**: no structured agent path changes.
- **VIII Ownership — PASS**: search material, step7 finalization, graph persistence, and consumer
  normalization remain with their existing owners.

Post-design re-check: PASS. The cross-service change is explicitly versioned in
`contracts/affects-table-v2.md`.

## Project Structure

```text
search/corpus/embed_material.py
search/corpus/corpus.py
search/ranking/recall.py
search/ranking/rerank.py
search/ranking/retriever.py
search/query/judge.py
pipeline/models/analysis_context.py
pipeline/models/code_analysis_output.py
pipeline/steps/step7_analyze_code/
graph/schema/graph_edges.py
graph/edges/affects_table.py
pipeline/execution/graph_delta_stream.py
tests/search/
tests/pipeline/step7_analyze_code/
tests/graph/
```

**Structure Decision**: extend narrow existing owners; do not add a generic shared utility
package. Architect changes are documented and implemented under its own spec 050.

