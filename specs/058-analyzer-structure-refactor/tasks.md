# Tasks: Analyzer Structure Refactor

**Input**: Design documents from `specs/058-analyzer-structure-refactor/`

**Prerequisites**: `spec.md`, `plan.md`, `research.md`, `data-model.md`, `contracts/`, `quickstart.md`

**Tests**: Required. Every movement slice has focused import/unit gates and the final phase has the complete
semantic and integration regression battery.

## Phase 1: Setup and Frozen Evidence

- [x] T001 Write and validate SpecKit specification and planning artifacts in `specs/058-analyzer-structure-refactor/`
- [x] T002 Freeze all 503 baseline paths in `specs/058-analyzer-structure-refactor/audit-baseline.txt`
- [x] T003 Initialize one pending row per baseline file in `specs/058-analyzer-structure-refactor/audit-ledger.tsv`
- [x] T004 Inventory static imports and planned paths in `specs/058-analyzer-structure-refactor/move-map.tsv`; dynamic/config/CI/workspace classification continues in T009
- [x] T005 Record pre-move unit, byte-identical, gold and service contract baselines in `specs/058-analyzer-structure-refactor/regression-evidence.md`
- [x] T006 Verify ignore coverage without hiding source or evidence files in `./.gitignore` and `./.dockerignore`

---

## Phase 2: Foundational Contracts and Audit Rules

- [x] T007 Add audit-ledger completeness and old-active-reference tests in `tests/structure/test_refactor_contract.py`
- [x] T008 Add import smoke coverage for public entry points in `tests/structure/test_public_imports.py`
- [x] T009 Classify dynamic/external consumers and deletion candidates in `specs/058-analyzer-structure-refactor/dynamic-consumers.tsv`
- [x] T010 Amend Constitution III/V target paths and bump version in `.specify/memory/constitution.md`

**Checkpoint**: Frozen baseline, consumer map and failing migration contracts exist before any code move.

---

## Phase 3: User Story 1 — Explicit Graph and System Owners (Priority: P1)

**Goal**: Move the lowest-level configuration and graph responsibilities into unambiguous owners.

**Independent Test**: graph/schema and environment imports compile, focused graph tests pass, and active old graph/config imports are zero.

- [x] T011 [US1] Move `shared/config/env_catalog.py` to `system/env_catalog.py` and update every runtime/config/test consumer
- [x] T012 [US1] Move graph models and relation/ID/constraint definitions into `graph/schema/` from `data_models/` and `shared/neo4j/`
- [x] T013 [US1] Move Neo4j connection/client and node/edge writers from `shared/neo4j/` into `graph/`
- [x] T014 [US1] Merge `shared/neo4j/merge.py` and `shared/neo4j/props.py` into `graph/node_upsert.py` with focused parity tests
- [x] T015 [US1] Move graph tests into `tests/graph/` and pass graph/schema/import tests
- [x] T016 [US1] Reconcile graph/system rows and evidence in `specs/058-analyzer-structure-refactor/audit-ledger.tsv`

---

## Phase 4: User Story 1 — Cohesive LLM Ownership (Priority: P1)

**Goal**: Put all LLM execution, prompts, agents, skills and configs under one predictable root without changing bytes or schemas.

**Independent Test**: prompt hashes and common prefixes pass byte-identical checks, structured agents import, and old LLM asset paths are inactive.

- [x] T017 [US1] Move `shared/llm/` into `llm/client/` subowners and update middleware/runner consumers
- [x] T018 [US1] Move `shared/content_text.py` to `llm/client/message_content.py` and preserve its three consumers and tests
- [x] T019 [US1] Move LLM-only prompt logging helpers from `shared/log/` into `llm/client/prompt_logging/`
- [x] T020 [US1] Move `prompts/`, `skills/`, `llm_configs/` and `agent_tools/` into `llm/{prompts,skills,configs,agents}/`
- [x] T021 [US1] Move prompt hashes to `llm/prompts/prompt_hashes.json`, regenerate only for path-set changes and pass byte-identical verification
- [x] T022 [US1] Pass structured-agent, middleware, budget, prompt and import focused tests under `tests/llm/`
- [x] T023 [US1] Reconcile all LLM rows and evidence in `specs/058-analyzer-structure-refactor/audit-ledger.tsv`

---

## Phase 5: User Story 1 — Pipeline Responsibility Map (Priority: P1)

**Goal**: Consolidate strategy, execution, models and seven analysis steps under `pipeline/`.

**Independent Test**: both framework and DBMS pipeline declarations build without strategy branches; step tests pass; old pipeline/shared imports are inactive.

- [ ] T024 [US1] Move `analysis_pipelines.py` into `pipeline/analysis_pipelines.py` and preserve zero-branch strategy declarations
- [ ] T025 [US1] Merge language extension, ontology and strategy detection into `pipeline/programming_languages.py` with adjacent `ontology_roles.yaml`
- [ ] T026 [US1] Move workflow execution modules into `pipeline/execution/` and absorb `step_utils.py` into `run_executor.py`
- [ ] T027 [US1] Merge `analysis_run.py` and `codebase.py` into `pipeline/data_models/analysis_meta.py` and move remaining analysis models
- [ ] T028 [US1] Move `analysis_flow/` into `pipeline/steps/` and update every internal, API, script and external runner consumer
- [ ] T029 [US1] Move single-owner helpers into their step: DDL parser, call classifier, example mapping builder/writer and AST identity registry
- [ ] T030 [US1] Move `domain_terms.py`, `source_file_cache.py` and Catalog client into explicit `pipeline/` owners
- [ ] T031 [US1] Move all pipeline tests into mirrored `tests/pipeline/` paths and pass framework/DBMS step tests
- [ ] T032 [US1] Reconcile all pipeline rows and evidence in `specs/058-analyzer-structure-refactor/audit-ledger.tsv`

---

## Phase 6: User Story 1 — Search, API, Logging and Scripts (Priority: P1)

**Goal**: Finish active runtime placement and remove obsolete top-level implementation roots.

**Independent Test**: REST/MCP/search entry points import, focused tests pass, and no active old root remains.

- [ ] T033 [US1] Refactor search modules into `search/{ranking,corpus,query}/` with approved descriptive filenames
- [ ] T034 [US1] Move operational logging into `log/` and preserve error/NDJSON/progress contracts
- [ ] T035 [US1] Move `cli_scripts/` into `scripts/{graph_maintenance,analysis,llm_maintenance}/`
- [ ] T036 [US1] Update `api/`, root `main.py`, Docker, deploy and workspace/goal-verify consumers for final internal paths
- [ ] T037 [US1] Move search/API/eval tests and gold assets into `tests/{search,api,eval}/`
- [ ] T038 [US1] Remove empty `shared/`, `analysis_flow/`, `agent_tools/`, `data_models/`, `llm_configs/`, `prompts/` and `cli_scripts/` roots only after active old-reference search returns zero
- [ ] T039 [US1] Reconcile search/API/log/scripts/root rows in `specs/058-analyzer-structure-refactor/audit-ledger.tsv`

---

## Phase 7: User Story 3 — Full-File Quality Audit (Priority: P2)

**Goal**: Resolve every baseline and new-file audit row using the complete maintenance principles.

**Independent Test**: baseline count is 503, every new file is covered, verdicts are valid, and pending count is zero.

- [ ] T040 [US3] Audit all runtime Python files for responsibility, DRY/SSOT, naming, dependency direction and dead code in `specs/058-analyzer-structure-refactor/audit-ledger.tsv`
- [ ] T041 [US3] Audit exception/logging, timeout/retry/cancel/shutdown, transaction/idempotency/concurrency and false-success behavior
- [ ] T042 [US3] Audit I/O/query bounds, caching, performance, credential/secret exposure, input and query security boundaries
- [ ] T043 [US3] Audit sample hardcoding, experimental artifacts, scripts, routes, dependencies and compatibility shims in `specs/058-analyzer-structure-refactor/audit-ledger.tsv`
- [ ] T044 [US3] Review the eight nominated large files and record `split` or `no-violation` evidence in `specs/058-analyzer-structure-refactor/audit-ledger.tsv`
- [ ] T045 [US3] Audit prompts, YAML/JSON, Docker/deploy/config, README, specs and baseline files including immutable-history classification
- [ ] T046 [US3] Add every new R1 file to the ledger and pass `tests/structure/test_refactor_contract.py` with zero pending rows

---

## Phase 8: User Story 2 — Full Regression and Handoff (Priority: P1)

**Goal**: Prove semantic and service behavior survived the refactor and document the final truth.

**Independent Test**: every required gate in quickstart passes with captured evidence.

- [ ] T047 [US2] Run full unit/import/compile tests and record results in `specs/058-analyzer-structure-refactor/regression-evidence.md`
- [ ] T048 [US2] Run prompt byte-identical and hash-coverage checks against final `llm/prompts/`
- [ ] T049 [US2] Run the 057 search gold battery and require at least 29/30
- [ ] T050 [US2] Run a clean shopmall reanalysis and compare graph/meaning results to the approved baseline
- [ ] T051 [US2] Run live REST, MCP, central UI and Architect legacy-evidence/canvas smoke scenarios
- [ ] T052 [US2] Update `./README.md`, `../../goal-verify/runners/` and `../../goal-verify/docs/handoffs/` to the verified final structure
- [ ] T053 [US2] Run old-path/name search, diff check, secret check and independent self-contradiction review
- [ ] T054 [US2] Commit and push verified R1 slices, then confirm local/origin hashes and clean worktrees

---

## Dependencies & Execution Order

- Setup and Foundational block every move.
- Graph/System → LLM → Pipeline → Search/API/Log/Scripts follows dependency direction.
- Audit rows are reconciled after each slice; the cross-cutting audit then proves no file escaped classification.
- Full regression begins only after all active imports and tests use final paths.
- R1B must not start until R1 commits are pushed and all required R1 gates are green.

## Implementation Strategy

Each movement phase is one reversible vertical slice: write/confirm focused tests, `git mv`, update all consumers,
run import and focused tests, search old active references, then update the audit ledger. Do not batch unrelated
behavior changes into a move. Commit logical verified slices and push normally after checking that origin has not
advanced; merge new collaborator work without force-pushing.
