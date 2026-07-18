# Implementation Plan: Analyzer Structure Refactor

> Historical migration plan. Paths below describe the R1 move sequence, not
> the final runtime layout. Spec 062 and `D:/work/robo/세서비스-최종트리.md`
> supersede its intermediate names.

**Branch**: `main` | **Date**: 2026-07-16 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `specs/058-analyzer-structure-refactor/spec.md`

## Summary

Rebuild the Analyzer repository around the approved v10 responsibility map while preserving every public and
semantic contract. Freeze the 503-file baseline, maintain a file-by-file audit ledger, move code in dependency-order
vertical slices with `git mv`, and prove each slice locally before the full unit, gold, reanalysis, REST/MCP/UI and
external-runner regression battery. Current-code evidence overrides stale deletion assumptions: `content_text.py`
and `node_registry.py` remain active and move to explicit owners; `module_test/` is already absent.

## Technical Context

**Language/Version**: Python 3.11

**Primary Dependencies**: FastAPI, LangGraph/LangChain, Pydantic, Neo4j driver, PyYAML

**Storage**: Neo4j plus filesystem AST/input, prompt and NDJSON artifacts

**Testing**: pytest/unittest, deterministic gold runners, live REST/MCP/UI and Playwright verification

**Target Platform**: Windows development and Linux container/service runtime

**Project Type**: Python analysis pipeline and web/MCP service

**Performance Goals**: no material runtime or token-cost regression from baseline; preserve prompt cache eligibility

**Constraints**: behavior-preserving refactor; 29/30 search gold floor; prompt bytes pinned; no silent failure;
no long-lived compatibility copies; specs and baselines remain historical originals

**Scale/Scope**: 503 tracked baseline files, 210 Python files, about 650 internal import references plus
external workspace/deploy/goal-verify consumers

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- **I Determinism-First**: PASS. No analysis algorithm changes are planned; audit rejects new LLM use for deterministic work.
- **II Zero-Branch Strategy**: PASS. `analysis_pipelines.py` moves into `pipeline/` without adding strategy branches.
- **III Single Source of Truth**: PASS after constitution amendment. `shared/config/env_catalog.py` becomes
  `system/env_catalog.py`; relationship names become `graph/schema/rel_types.py`; all consumers move in the same slice.
- **IV No Silent Failure**: PASS. Full-file audit includes broad/empty exception and false-success checks.
- **V KV-Cache-Friendly Prompts**: PASS if prompt moves preserve bytes and regenerated hashes pass byte-identical verification.
- **VI Schema Restraint**: PASS. Public Pydantic and MCP output contracts remain unchanged.
- **VII Submit-Tool Wrap**: PASS. Agent runner moves without changing structured output mechanism.
- **VIII Explicit Ownership and Dependency Direction**: PASS. Component write cohesion remains intact; new merges require consumer evidence.
- **Cross-service contracts**: PASS only after AST input, Neo4j schema, Catalog HTTP and Architect MCP smoke tests.
- **Cost & safety**: PASS only if budget breaker and run lifecycle tests remain green. Destructive graph lifecycle is not
  redesigned in R1; ownership/lifecycle replacement belongs to R1B and is documented as a known constraint.

Post-design re-check: PASS with two corrected assumptions. Active `content_text.py` and `node_registry.py` are moved,
not deleted. The approved language-knowledge merge remains, but its evidence is shared cohesion and SSOT rather than
the stale claim that all three source files mutually import one another.

## Project Structure

### Documentation (this feature)

```text
specs/058-analyzer-structure-refactor/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/preserved-contracts.md
├── audit-baseline.txt
├── audit-ledger.tsv
└── tasks.md
```

### Source Code (repository root)

```text
api/                              # HTTP and MCP entry points only
graph/
├── schema/                       # graph nodes, edges, relation types, constraints, IDs
├── nodes/                        # node writers
├── edges/                        # relationship writers
├── neo4j_client.py
├── connection_context.py
└── node_upsert.py
llm/
├── client/{runners,usage,middleware,prompt_logging}/
├── prompts/{framework,dbms,common}/
├── agents/
├── skills/
└── configs/
pipeline/
├── analysis_pipelines.py
├── programming_languages.py
├── ontology_roles.yaml
├── domain_terms.py
├── source_file_cache.py
├── catalog_client.py
├── execution/
├── data_models/
└── steps/step1_parse_ddl ... step7_post_processing/
search/{ranking,corpus,query}/
log/
scripts/{graph_maintenance,analysis,llm_maintenance}/
system/env_catalog.py
tests/{pipeline,graph,search,api,eval}/
specs/ · .specify/ · README.md · requirements.txt · Dockerfile
```

**Structure Decision (historical)**: This R1 slice used the then-approved v10
handoff. The handoff path has since been retired; the current target is
`D:/work/robo/세서비스-최종트리.md`. `shared/`, `analysis_flow/`, `agent_tools/`, `data_models/`,
`llm_configs/`, root `prompts/` and `cli_scripts/` disappear after all consumers move. Internal `deploy/` is not
removed in R1; cross-service deployment cleanup is the later R1B slice after root-deploy consumption is proven.

## Migration Order

1. Freeze baseline and audit inventory; record external consumers and contract tests.
2. Move graph schema/client/writers and update all consumers; verify graph unit and import gates.
3. Move LLM client, middleware, prompt logging, prompts, agents, skills and configs; preserve prompt bytes and hashes.
4. Move pipeline models/execution/steps and local single-consumer helpers; merge only the three approved cohesive groups.
5. Move search, API-adjacent logging, scripts and tests; update external goal-verify/deploy/workspace references.
6. Resolve every audit-ledger row and every large-file split review; remove empty old directories only after old-ref 0.
7. Amend constitution and README, then run complete regression and self-contradiction checks.

## Audit Method

`audit-baseline.txt` freezes 503 files from commit `4a1bb126...`. `audit-ledger.tsv` must contain exactly one row per
baseline file plus rows for every new R1 file. Each row records path, responsibility, static/dynamic consumers,
maintainability/DRY/naming/dependency/error/performance/security/generality/docs findings, final verdict and test evidence.
Historical specs may pass as immutable history, but their runtime references are still scanned. Completion requires
baseline count match, new-file coverage and zero `pending` rows.

## Complexity Tracking

No constitution violation is accepted. Constitution Principle III and V path text must be amended with a minor
version bump because the same single truths move; the principles themselves do not change.
