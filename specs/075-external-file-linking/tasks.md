# Tasks: External File Linking

**Input**: `spec.md`, `research.md`, `data-model.md`, `contracts/`, `plan.md`, `move-map.tsv`, `consumer-map.md`

**Rule**: Every implementation task includes red/focused/negative/contract/full/diff evidence. A checked box without
an entry in `verification-evidence.md` is not completion evidence.

## Phase SDD — Contract lock

- [x] T001 Freeze Analyzer/Frontend/ANTLR/Architect branch, HEAD and WIP in `verification-evidence.md`.
- [x] T002 Create and review `spec.md` with P0~P8, final 8-step tree, user scenarios and measurable outcomes.
- [x] T003 Create `research.md`, `data-model.md` and four `contracts/*.md` from current code and HANDOFF §12.
- [x] T004 Create `move-map.tsv` and `consumer-map.md`; confirm every active old-step consumer is classified.
- [x] T005 Create `plan.md`, `quickstart.md` and this `tasks.md` with Constitution Check and phase evidence gates.
- [x] T006 Run SDD analysis: placeholders 0, requirement→task coverage 100%, contradictory names/trees 0,
      missing planned parent paths 0, prohibited-copy plan 0. Record results.

**Checkpoint**: source code remains unchanged except spec 075 files; 074 WIP and `.specify/feature.json` unchanged.

## P0 — Graph, ID, state and stream contracts

### Red tests

- [x] T010 [P0] Add `tests/graph/test_external_graph_contract.py` for file namespace, same-key/different-file collision,
      path escape, label allowlist, owner stamping, idempotent writers and missing endpoints.
- [x] T011 [P0] Extend `tests/pipeline/execution/test_graph_delta_contract.py` with every new node/edge exact wire shape,
      blank endpoint and unsupported type failures.
- [x] T012 [P0] Add WorkflowState ownership/reducer tests in `tests/pipeline/execution/test_external_state_contract.py`.
- [x] T013 [P0] Run T010–T012 and record expected failures before implementation.

### Models and writers

- [x] T014 [P0] Add `FILE_NS` and project/chunk/unresolved/candidate ID helpers to `graph/schema/node_ids.py`.
- [x] T015 [P0] Add ProjectFile, ExternalChunk, DocumentNode, UnresolvedRef, CandidateLink to
      `graph/schema/graph_nodes.py` with validation builders where needed.
- [x] T016 [P0] Add typed external relationship dataclasses to `graph/schema/graph_edges.py` and constants to
      `graph/schema/rel_types.py`.
- [x] T017 [P0] Add node writers under `graph/nodes/` using `graph/node_upsert.py`; do not duplicate merge helpers.
- [x] T018 [P0] Add `graph/edges/external_links.py`; reuse/broaden `graph/edges/reads_writes.py` for SQL sources.
- [x] T019 [P0] Register constraints and legacy ownership labels in `graph/schema/constraints.py` and update
      `tests/graph/test_graph_ownership.py`.
- [x] T020 [P0] Add state keys/reducers in `pipeline/execution/workflow_state.py`.
- [x] T021 [P0] Register node labels/display props and external edges in
      `pipeline/execution/graph_delta_stream.py` without weakening fail-closed behavior.

### P0 verification

- [x] T022 [P0] Pass external graph, ownership, stream and state focused tests.
- [x] T023 [P0] Pass full Analyzer unittest with `-t .` and compileall (known 074 audit baseline excluded).
- [x] T024 [P0] Search prohibited copies/imports 0 and audit diff limited to spec/P0 contract owners.
- [x] T025 [P0] Record P0 evidence and current Git/WIP; do not continue on regression.

## P1 — Final 8-step renumber

- [x] T030 [P1] Re-run baseline full tests and active consumer search; update move map if current code changed.
- [x] T031 [P1] Obtain one destructive `git mv` batch confirmation immediately before moves.
- [x] T032 [P1-A] `git mv` step7_post_processing source/tests to step8; update all active consumers; focused tests;
      active old path grep 0 for this slice.
- [x] T033 [P1-B] `git mv` step6_analyze_code source/tests to step7; update consumers; focused tests; old path 0.
- [x] T034 [P1-C] `git mv` step5_build_context source/tests to step6; update consumers; focused tests; old path 0.
- [x] T035 [P1-D] `git mv` step4_linking source/tests to step5; update consumers; focused tests; old path 0.
- [x] T036 [P1-E] `git mv` step3_scan_project source/tests to step4; update consumers; focused tests; old path 0.
- [x] T037 [P1] Update `pipeline/strategies.py`, WorkflowState comments, artifact/log component strings, README,
      scripts and structure tests to final numbering.
- [x] T038 [P1] Pass Framework/DBMS strategy contracts, full unittest, compileall and active old step path 0
      (known 074 audit baseline excluded).
- [x] T039 [P1] Record file-history, move, test and diff evidence; confirm historical narrative specs were not rewritten.

## P2 — Step3 external loading

### Red tests and dependencies

- [x] T040 [P2] Add direct runtime dependencies to `requirements.txt` with import tests: charset-normalizer,
      javaproperties, regex; avoid optional document imports at module import time.
- [x] T041 [P2] Add encoding tests for BOM/UTF-8/CP949/EUC-KR/false declaration/binary/invalid bytes.
- [x] T042 [P2] Add inventory/path tests for exclusions, symlink escape, permissions, empty/binary/unknown statuses.
- [x] T043 [P2] Add chunk token/size/reassembly/hash/order tests and run red.

### Loader implementation

- [x] T044 [P2] Add `pipeline/steps/step3_load_external/encoding_reader.py` using SourceFileCache bytes/text boundary.
- [x] T045 [P2] Add deterministic inventory and stable ordered classification in `inventory.py`.
- [x] T046 [P2] Add `chunking.py` with 1,200-char anchor/parts and content hash.
- [x] T047 [P2] Port/adapt XML parser with entity/network/comment/CTE/SQL defenses under `parsers/xml_universal.py`.
- [x] T048 [P2] Port/adapt JSON/TOML/INI/properties/env/CSV/TSV/YAML parser in `parsers/config_parser.py`.
- [x] T049 [P2] Add Markdown document hierarchy/chunking in `parsers/markdown_doc.py`.
- [x] T050 [P2] Add custom pattern application boundary stub that only accepts validated ExternalSkill rules.
- [x] T051 [P2] Implement `load_external_step.py` with existing configurable, progress scope, graph writers and stream.
- [x] T052 [P2] Insert step3 in Framework and DBMS strategies; prove state non-collision before AST/loader parallel tuple.

### P2 verification

- [x] T053 [P2] Port/run config_formats, encoding, stability and chunk-budget suites against isolated fixtures.
- [x] T054 [P2] Pass loader integration with real project inventory and verify PROJECT_FILE count/status against source.
- [x] T055 [P2] Pass full unittest/compile, active path grep and diff audit; record P2 evidence.

## P3 — Step5 external linking

### Red tests

- [x] T060 [P3] Add direct literal, comments, partial substring, dynamic concat, duplicate and blank-key tests.
- [x] T061 [P3] Add SQL CTE/string/comment/reserved/MERGE/UPDATE-SET/table-not-DDL adversarial tests.
- [x] T062 [P3] Add Variable/Constant-based immutable propagation positive and reassignment/composition negative tests.
- [x] T063 [P3] Add key-index exhaustive parity and scale tests; run red.

### Linker implementation

- [x] T064 [P3] Add `key_index.py` and exact quoted-key matcher under `step5_linking/link_external/`.
- [x] T065 [P3] Add safe SQL table reference extraction preserving playground adversarial fixes.
- [x] T066 [P3] Add Variable/Constant-based one-step propagation without source toy parser/re-read duplication.
- [x] T067 [P3] Implement `link_external_step.py`, typed edge persistence/stream, unresolved production and READS/WRITES chain.
- [x] T068 [P3] Register link step in both strategies after AST+external load and before build-context.

### P3 verification

- [x] T069 [P3] Port/run gates, shopmall ground truth, adversarial v1/v2/v3, real_link, real_link_ukesa,
      real_multi, scale and idempotency suites using actual step2 nodes.
- [x] T070 [P3] Compare link triples and evidence per answer-key item; counts alone are insufficient.
- [x] T071 [P3] Pass full unittest/compile, old/prohibited path grep and diff audit; record P3 evidence.

## P4 — External Agent and skills

- [x] T080 [P4] Add red tests for framework/external catalog isolation, schema extra-field rejection,
      overfit/unsafe/timeout/empty/duplicate rules, R<=2, consensus and cancellation.
- [x] T081 [P4] Obtain confirmation if the six-skill `git mv` is treated as destructive batch.
- [x] T082 [P4] Move six framework skills under `agents/skills/framework/`; update one framework root and all consumers.
- [x] T083 [P4] Add external skill root, ExternalSkill output schema and flat prompt assets with hash coverage.
- [x] T084 [P4] Implement timeout-capable deterministic rule validator/application using declared `regex` dependency.
- [x] T085 [P4] Implement `agents/external.py` using structured submit runner,
      `gather_with_warmup`, shared semaphore, budget, R<=2 and n7/agree4.
- [x] T086 [P4] Implement atomic accepted-skill persistence and zero-LLM reload.
- [x] T087 [P4] Port/run agent_battery, agent_consensus, agent_loop and adversarial regex tests.
- [x] T088 [P4] Verify framework prompt/scan outputs byte/semantic parity and cross-catalog result 0.
- [x] T089 [P4] Pass full unittest/prompt hash/compile, `format|filetype|format_skill_agent` active grep 0; record evidence.

## P5 — Step6 context injection

- [x] T090 [P5] Add red tests for per-node relevance, part order/hash, duplicate evidence, token bounds and no project-wide injection.
- [x] T091 [P5] Extend context model/builder under `step6_build_context/` with bounded external evidence.
- [x] T092 [P5] Feed new context values through existing Framework/DBMS prompt value owners without duplicating prefix text.
- [x] T093 [P5] Verify prompt records contain exact linked evidence and omit unlinked/candidate content.
- [x] T094 [P5] Run token budget, cancellation, Framework/DBMS analysis and prompt hash regression.
- [x] T095 [P5] Pass full unittest/compile/active grep/diff audit; record P5 evidence.

## P6 — Frontend and live stream

- [x] T100 [P6] Freeze Frontend WIP and target-file hashes; create the next non-conflicting Frontend SDD.
- [x] T101 [P6] Add red pure tests for progress wording, name/icon resolution, hidden sets and relationship projection.
- [x] T102 [P6] Update only `src/features/code-analysis/analyzer.ts` and
      `src/shared/utils/graphVisibility.ts` plus owned tests.
- [x] T103 [P6] Run strict TypeScript, build and focused frontend tests without overwriting dirty package/graphCanvas files.
- [x] T104 [P6] Live Playwright: step3/step5 progress, default hidden, show-all, exact node/edge inventory,
      unrelated relationship 0, console/network error 0.
- [x] T105 [P6] Recompare all pre-existing Frontend WIP hashes/diffs and record P6 evidence.

## P7 — Document adapters and Korean PoC

- [x] T110 [P7] Verify official current APIs/licenses and record selected optional dependency versions.
- [x] T111 [P7] Add adapter availability/status contract and lazy imports for PDF/XLSX/DOCX/HWP/HWPX/OCR.
- [x] T112 [P7] Implement PyMuPDF, openpyxl, python-docx and HWP/HWPX adapters with bounds/timeouts.
- [x] T113 [P7] Implement local PaddleOCR-VL adapter for scanned PDF/image when installed.
- [x] T114 [P7] Add Korean text/table/image fixtures and exact text/order/hash ground truth.
- [x] T115 [P7] Integrate exact MENTIONS and candidate node creation through existing embed path only.
- [x] T116 [P7] Test malformed/encrypted/huge/missing-extra documents, CPU/memory and no-startup-import behavior.
- [x] T117 [P7] Pass full unittest/compile/active grep/diff audit; record P7 evidence and honest unsupported cases.

## P8 — Hardening, cleanup and end-to-end proof

- [x] T120 [P8] Prove all regex validation/application paths use timeout-capable engine and leave no live worker/task.
- [x] T121 [P8] Search all `has_xml_mapper` producers/consumers; remove API/model/README/tests only after consumer 0 proof.
- [x] T122 [P8] Organize all required xlink verification assets under Analyzer-owned tests/scripts; prohibited module copies 0.
- [x] T123 [P8] Run full Analyzer unittest, structure tests, prompt hash, compileall and dependency/import smoke.
- [x] T124 [P8] Run real Neo4j Analyzer Framework and DBMS analyses with isolated input/output; verify no failed events.
- [x] T125 [P8] Compare source→AST→chunk→graph→context→REST/MCP per answer-key item and graph fingerprint/idempotency.
- [x] T126 [P8] Run Frontend live E2E and capture screenshots/event/network/console evidence.
- [x] T127 [P8] Final active grep 0: old steps, format/filetype, prohibited copies, has_xml_mapper, compatibility shim, temp files.
- [x] T128 [P8] Final Git diff/status audit in every repository; confirm unrelated WIP unchanged.
- [x] T129 [P8] Cross-check spec↔plan↔tasks↔code↔evidence requirement by requirement; leave any weak item unchecked.

## Dependencies and checkpoints

- SDD blocks P0; P0 blocks all implementation.
- P1 blocks P2–P5 because final paths must exist before new files are added.
- P2 and actual step2 state block P3; P3 blocks P5.
- P2 custom format boundary blocks P4.
- P0 stream blocks P6; P3 provides live links for P6 proof.
- P2 document model blocks P7.
- P8 requires all prior phases.

No Phase transition occurs with a failed focused/full contract, nonzero active old-path result for that slice, or
unrecorded WIP drift.
