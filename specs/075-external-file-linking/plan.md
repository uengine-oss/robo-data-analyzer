# Implementation Plan: External File Linking

**Branch**: `main` | **Date**: 2026-07-21 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `specs/075-external-file-linking/spec.md`

## Summary

Playground에서 검증한 종류 무관 외부파일 inventory→parsing→anchor/parts chunking→직접사용 linking→
context→graph/UI 기능을 Analyzer의 현재 7-step 구조에 네이티브하게 병합한다. 먼저 graph/ID/state/stream
계약을 추가하고, 기존 step3~7과 tests를 `git mv`로 step4~8에 이동한 뒤, 신규 step3 loader,
step5 external linker, framework/external skill 분리, step6 context, Frontend, 문서 adapters, 전체 E2E 순으로
구현한다. playground의 중복 AST/graph/LLM/env 계층은 복사하지 않는다.

## Technical Context

**Language/Version**: Python 3.12 local runtime; repository compatibility follows current Analyzer declaration

**Primary Dependencies**: FastAPI, LangGraph, Neo4j, lxml, PyYAML, sqlglot, existing structured-agent/LLM stack;
new direct light dependencies `charset-normalizer`, `javaproperties`, `regex`; document adapters are optional extras

**Storage**: Analyzer-owned Neo4j graph, run-output artifacts, atomic external SKILL files

**Testing**: authoritative unittest discovery with `-t .`, compileall, prompt hash, xlink verification assets,
live Analyzer/Neo4j, REST/MCP, Frontend unit/build/Playwright

**Target Platform**: Windows development and Linux service runtime; no Windows-only production implementation

**Project Type**: Python analysis service plus Vue/TypeScript graph consumer

**Performance Goals**: deterministic 5,000-file inventory under prior playground scale envelope; key-index result parity;
n=7 agent consensus near one provider latency under configured concurrency; no unbounded chunk/context growth

**Constraints**: no graph false positives, no silent file loss, no daemon regex workers, owner-scoped graph writes,
run budget/cancel preservation, current user WIP untouched, shared `project/data` read-only

**Scale/Scope**: 15-project/3,072 non-source file census, 68 extensions, 1,200-char chunks, P0~P8 and two repositories

## Constitution Check

- **I Determinism-First**: PASS. Inventory, known parsers, chunking, direct matching, constants and graph are LLM 0.
  LLM is limited to unknown eligible text formats and document embeddings.
- **II Zero-Branch Strategy**: PASS. Final pipeline order changes only in `pipeline/strategies.py`; step tuples express
  load concurrency. Steps contain no `strategy.name` branch.
- **III Single Source of Truth**: PASS. IDs, relation names, settings, graph writers, visibility and skill directories each
  have one owner. No copied playground infrastructure.
- **IV No Silent Failure**: PASS. File-level outcomes are explicit; invalid phase inputs and stream/graph contract failures raise.
- **V KV-Cache-Friendly Prompts**: PASS if external fanout uses byte-identical system prefix and prompt hash coverage.
- **VI Schema Restraint**: PASS. ExternalSkill is flat and bounded; graph dataclasses use explicit fields.
- **VII Submit-Tool Wrap**: PASS. External Agent reuses the existing structured submit-tool runner.
- **VIII Ownership/Dependency Direction**: PASS. parsing under step3, linking under step5, context under step6,
  contracts under graph/llm/source owners.
- **Cross-service**: Analyzer NDJSON labels/relationships change Frontend consumption. A Frontend SDD is required before P6.
- **Cost/Safety**: PASS if shared budget/semaphore/cancellation and owner-scoped wipe tests remain green.

Post-design re-check: PASS. The only deliberate new namespace is `file:` because current `code:external:` is already
owned by external CALLS stubs. This is separation, not duplicate truth.

## Target Project Structure

```text
robo-data-analyzer/
├── source/{file_cache.py,path_resolver.py}              # existing, reused
├── pipeline/
│   ├── strategies.py                                    # final 8-step declarations
│   ├── execution/{workflow_state,graph_delta_stream}.py # contract extensions
│   └── steps/
│       ├── step1_parse_ddl/                              # retained
│       ├── step2_load_ast/                               # retained
│       ├── step3_load_external/                          # new
│       │   ├── load_external_step.py
│       │   ├── inventory.py
│       │   ├── chunking.py
│       │   ├── encoding_reader.py
│       │   └── parsers/{xml_universal,config_parser,markdown_doc,document_adapters}.py
│       ├── step4_scan_project/                           # git mv from step3
│       │   └── external_agent.py                         # new sibling of scan_agent
│       ├── step5_linking/                                # git mv from step4
│       │   └── link_external/{link_external_step,key_index,const_propagation,sql_tables}.py
│       ├── step6_build_context/                          # git mv from step5
│       ├── step7_analyze_code/                           # git mv from step6
│       └── step8_post_processing/                        # git mv from step7
├── graph/
│   ├── schema/{graph_nodes,graph_edges,rel_types,node_ids,constraints}.py
│   ├── nodes/{external_chunk,project_file,document,unresolved_ref,candidate_link}.py
│   └── edges/{external_links,reads_writes}.py
├── llm/
│   ├── agents/{skill_catalog,external_output}.py
│   ├── prompts/external/
│   └── skills/{_templates,framework/,external/}
├── tests/
│   ├── graph/
│   ├── pipeline/step3_load_external/
│   ├── pipeline/step4_scan_project/
│   ├── pipeline/step5_linking/
│   ├── pipeline/step6_build_context/
│   ├── pipeline/step7_analyze_code/
│   └── pipeline/step8_post_processing/
└── specs/075-external-file-linking/
```

Frontend P6 target is limited to current code-analysis stream/visibility owners and new tests.

## Phase Plan

### SDD Gate

Create spec, research, data model, four contracts, move/consumer maps, plan, quickstart and tasks. Run placeholder,
requirement traceability, path-existence and contradiction checks. No source edit before this gate passes.

### P0 — Graph, ID, state and stream contracts (6–10h)

1. Add red tests for namespace collision, stable labels, writer ownership, typed edges, stream parity and WorkflowState.
2. Add `file:` ID helpers, node/edge dataclasses and relation constants.
3. Add owner-stamped node/edge writers through existing graph helpers.
4. Register constraints, ownership labels, `_FIXED_NODE_LABEL`/display props and `_EDGE_SPEC`.
5. Add state keys with reducer ownership.
6. Run focused graph/stream/state tests, full unittest, compile and prohibited-copy grep.

**Exit evidence**: exact wire fixtures, duplicate replay, full baseline, source diff limited to contract owners.

### P1 — Final step renumber (8–14h, destructive move confirmation required)

1. Re-freeze Git/WIP and test baseline.
2. Ask once immediately before the `git mv` batch, per user destructive-action rule.
3. Move source and mirrored test directories in reverse dependency order:
   step7→8, step6→7, step5→6, step4→5, step3→4.
4. Update active imports, strings, artifact classification, runtime component names, README, scripts and tests in each slice.
5. After every move run focused tests and active old-path grep 0 before the next move.

Historical specs remain unchanged and are excluded from active-path zero assertions.

### P2 — Step3 external loading (12–18h)

1. Add dependencies and encoding/path/inventory contracts.
2. Port only inventory, chunking, encoding and parser logic; adapt all reads to SourceFileCache/path resolver.
3. Add load step with processing scope, per-file progress, graph writers and node stream.
4. Register in both strategies after AST, then prove disjoint keys before optional parallel tuple.
5. Port stability/config/encoding/token-budget tests and real fixtures without touching shared project data.

### P3 — Step5 external linking (12–18h)

1. Port exact key linking, key index, SQL table extraction defenses and Variable/Constant-based one-step propagation.
2. Reuse actual step2 code nodes and variables; do not read toy Java functions.
3. Persist and stream typed links; reuse READS/WRITES for SQL/table and derived code/table.
4. Port real AST, independent answer key, adversarial, scale and idempotency suites.
5. Prove key-index output equality to exhaustive matching.

### P4 — External Agent and skill separation (8–12h)

1. `git mv` six existing framework skill folders under `framework/` after separate move confirmation if treated as batch.
2. Change framework skill root once; update all active readers/docs/tests in same slice.
3. Add `external/`, response schema, prompt and external_agent using shared runner/fanout/semaphore/budget.
4. Port custom pattern deterministic gate with `regex` timeout and R≤2/n7-agree4 control.
5. Prove cross-catalog isolation, atomic persistence and zero-LLM reload.

### P5 — Step6 context injection (8–14h)

1. Extend AnalysisContext with bounded external evidence owned by the context layer.
2. Reconstruct linked anchor parts by order/hash and inject only per-node relevant evidence.
3. Preserve common prompt bytes; add prompt inputs without duplicating system prefix.
4. Verify prompt records, token budgets, Framework/DBMS result parity and cancellation.

### P6 — Frontend and stream (10–16h)

1. Freeze Frontend WIP and target-file hashes; create its next non-conflicting SDD.
2. Add progress text, node icons/names and visibility constants in existing owners.
3. Add pure projection/stream tests and strict TypeScript/build checks.
4. Run live Analyzer UI Playwright for default hidden/show-all, node/link inventories, console/network 0.

### P7 — Document adapters and Korean PoC (16–28h)

1. Verify current official package APIs/licenses before adding optional dependency groups.
2. Implement lazy adapters for PDF, XLSX, DOCX, HWP/HWPX and OCR; missing extras produce explicit status.
3. Add Korean text/table/image fixtures and content/ordering/lineage checks.
4. Route document chunks to exact MENTIONS and existing embedding candidate path; never auto-promote candidates.
5. Run CPU/memory/timeout and malformed document tests.

### P8 — Production hardening and full verification (10–16h)

1. Replace all thread regex timeout paths with declared timeout-capable engine; verify no worker leak.
2. Remove `has_xml_mapper` after API/model/README/external consumer search proves obsolete.
3. Port/organize all xlink verification suites under Analyzer tests/scripts without prohibited modules.
4. Run full unittest, structure, prompt hash, compile, real Neo4j Analyzer analysis, REST/MCP and Frontend E2E.
5. Compare source→AST→chunks→graph→context→API→UI per answer-key item.
6. Final grep: active old steps 0, format/filetype 0, prohibited copies 0, has_xml_mapper 0, temp/shim 0.
7. Diff audit all repositories and record verified/failed/unverified evidence.

## Slice Gate

Every implementation slice follows:

`red test → minimal implementation → focused test → negative test → active old-path grep 0 → full contract subset → diff audit → evidence record`

Failure ends the current slice; it is not covered by later changes.

## Verification Evidence

Evidence is appended to `verification-evidence.md` with timestamp, repo HEAD/status, command, exit code, counts,
artifact path and verified/failed/unverified classification. Completed history is not added to the unrelated GOAL-D todo.

## Rollback

- Additive P0/P2/P3 changes are reverted by logical file/diff slice only, never `git reset --hard`.
- P1 moves are reversible `git mv` pairs recorded in `move-map.tsv`.
- Graph schema additions are backward compatible; no database data deletion is required for rollback.
- Feature flags are not introduced. If a phase cannot meet contract, it remains unmerged rather than hidden behind a flag.

## Complexity Tracking

No constitution violation is accepted. Optional document dependencies are adapters because unconditional OCR imports
would violate service startup reliability; the interface and status contract keep this complexity bounded.
