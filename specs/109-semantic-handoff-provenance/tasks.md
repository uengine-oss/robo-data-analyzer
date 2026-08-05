# Tasks: Semantic Search, Handoff, and Table-Effect Provenance

**Input**: Design documents from `/specs/109-semantic-handoff-provenance/`

**Prerequisites**: spec.md, research.md, data-model.md, plan.md,
contracts/affects-table-v2.md

## Phase 1: Baseline and Contract

- [x] T001 Audit current search material, CALLS handoff, retry policy, scanner authority, graph
  persistence, and Architect consumers.
- [x] T002 Record cross-service AFFECTS_TABLE v2 contract and explicit UNKNOWN semantics.

## Phase 2: Semantic Retrieval

- [x] T003 [US1] Add failing search-text-only unit tests.
- [x] T004 [US1] Make embedding, corpus eligibility, BM25, structural semantic recall, CE,
  diversification text, and LLM judge use only search_text.
- [x] T005 [US1] Run focused search tests and record evidence.

## Phase 3: CALLS Handoff

- [x] T006 [US2] Add `AnalysisContext.search_text` and propagate it from both Framework and DBMS
  summary application paths.
- [x] T007 [US2] Rename the active prompt/input contract to `callee_search_texts` and format only
  callee search text.
- [x] T008 [US2] Prove no fallback to summary/one-liner and summary-only range compaction.

## Phase 4: Repetition Abort

- [x] T009 [US3] Add a failing one-attempt repetition-abort test.
- [x] T010 [US3] Make `RepetitionLoopAborted` non-retryable while retaining ordinary bounded retry.

## Phase 5: Effect Provenance and Role

- [x] T011 [US4] Make LLM op optional and add TRUNCATE support without adding provenance fields to
  the LLM schema.
- [x] T012 [US4] Implement one deterministic serializer for access/op/op_source and use it in
  Framework and DBMS batch fan-in.
- [x] T013 [US4] Persist and live-stream AFFECTS_TABLE v2.
- [x] T014 [US4] Replace `WRITE-then-READ` with `READ_WRITE` in schema, validation, and prompts.
- [x] T015 [US4] Add scanner/inferred/unresolved/contradiction and graph persistence tests.

## Phase 6: Cross-Service and Verification

- [x] T016 Complete paired Architect spec 050 consumer implementation and tests.
- [x] T017 Refresh intentional prompt hashes and run byte-identity verification.
- [x] T018 Run focused, full Analyzer, and Architect test suites.
- [x] T019 Audit final diff, preserve pre-existing spec 106–108 WIP, and write verification.md.
