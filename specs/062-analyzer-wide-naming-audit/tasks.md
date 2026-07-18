# Tasks

- [x] T001 Freeze full inventory, public imports, external consumers and runtime entrypoints.
- [x] T002 Rename the root settings owner and all active consumers without compatibility aliases.
- [x] T003 Audit every source file and all ambiguous file/class/function/variable names.
- [x] T004 Apply evidence-backed moves, renames, splits, merges and dead-code deletions.
- [x] T005 Reconcile ledger/move map/docs and prove old-name/missing-row zero.
- [x] T006 Run full static, unit, prompt, official runtime, REST/MCP/UI/Architect gates.
- [x] T029 Bind every current Analyzer runtime file to the GOAL-D final tree and resolve post-§1 conflicts explicitly.
- [x] T030 Implement the final source/external/pipeline/models/strategy/language ownership paths without shims.
- [x] T031 Implement the required Step2/Step4/Step6, graph, search, LLM and observability splits/deletions.
- [x] T032 Pass machine final-tree verification with missing 0, forbidden 0 and unclassified 0.

## Final evidence

- Final-tree verifier: Analyzer required paths 687; missing 0, forbidden 0,
  unexpected 0, unclassified 0 across the three-product inventory.
- Authoritative Analyzer suites: structure 18/18 and full 186/186; prompt hash
  26/26; compileall and diff-check pass.
- Final RWIS run `20260717_182434_d09f866e5048` completed all 14 steps with
  Catalog lineage 200, no failed events, prompt-record warnings, or checkpoint
  warnings.
- Live REST search and MCP list/detail transport pass against the final graph.
- [x] T007 Reject unmetered YAML/Agent LLM successes and verify missing-response/usage boundaries. Evidence: focused 7/7, root-qualified 81/81, structure 11/11.
- [x] T008 Scope Analyzer post-processing, search and MCP graph reads/writes to Analyzer-owned nodes. Evidence: focused 3/3, root-qualified 84/84, structure 11/11.
- [x] T009 Preserve fan-out cancellation and reject embedding result-count mismatches. Evidence: focused 10/10, root-qualified 87/87, structure 11/11.
- [x] T010 Reject unknown model pricing and invalid token/cost values while preserving explicitly configured zero-cost models. Evidence: focused 15/15, root-qualified 92/92, structure 11/11, compile/diff check PASS.
- [x] T011 Enforce exact LLM config and per-call override types, ranges and supported reasoning values. Evidence: focused 19/19, root-qualified 96/96, structure 11/11, compile/diff check PASS.
- [x] T012 Remove the unused agent output field, enforce exact supported schema values and centralize skill catalog reads. Evidence: active old-field references 0 except rejection test, focused 22/22, root-qualified 99/99, prompt hash 26/26, structure 11/11, compile/diff check PASS.
- [x] T013 Fail closed on missing/persistence-failed usage while charging already-incurred run cost. Evidence: focused 25/25, root-qualified 102/102, prompt hash 26/26, structure 11/11, compile/diff check PASS.
- [x] T014 Align todo/tool event ownership with real consumers and isolate optional diagnostic write failures. Evidence: focused 27/27, root-qualified 104/104, prompt hash 26/26, structure 11/11, compile/diff check PASS.
- [x] T015 Prevent optional prompt-recording failures from masking Agent outcomes or leaking invoke context. Evidence: focused 29/29, root-qualified 106/106, prompt hash 26/26, structure 11/11, compile/diff check PASS.
- [x] T016 Remove ambiguous substring price matching that can classify unknown paid models as free. Evidence: focused 30/30, root-qualified 107/107, prompt hash 26/26, structure 11/11, compile/diff check PASS.
- [x] T017 Reject invalid environment values and restrict run-output reset to an explicitly Analyzer-owned directory. Evidence: red tests reproduced 5 failures; focused 8/8, root-qualified 112/112, structure 16/16, prompt hash 26/26, compile/diff check PASS; unowned/corrupt-marker/file paths preserve sentinels.
- [x] T018 Bind NDJSON producer task cancellation and collection to the response iterator lifecycle for analyze and search. Evidence: both old routes failed red lifecycle tests; focused 3/3 and full 117/117 PASS; normal stream_end collects success and consumer close cancels/collects producer.
- [x] T019 Measure and design lossless NDJSON queue backpressure without breaking large graph streams. Evidence: old 100,000-event no-consumer queue retained 93.76 MiB; async high-water 1,024 delivered 100,000/100,000 in order with peak queue 1,024 and 0.27 MiB traced peak; full-queue disconnect cleanup regression, focused 17/17, full 142/142, structure 16/16, prompt 26/26, compile/diff PASS. See `ndjson-backpressure-evidence.md`.
- [x] T020 Apply logging policy with pre-existing handlers and make run file-handler attachment transactional. Evidence: both defects reproduced red; focused runtime event suite 5/5 and full 117/117 PASS; compile/prompt 26/26/diff check PASS.
- [x] T021 Preserve the original outcome when optional error/warning NDJSON mirroring fails. Evidence: old helper replaced the original error in red test; runtime events 7/7 at completion point and full 121/121 PASS.
- [x] T022 Reconcile documented NDJSON event types with producers and mirror product messages to the runtime log. Evidence: missing message mirror and error component argument collision reproduced red; event inventory reconciled, focused regression and full 129/129 PASS.
- [x] T023 Restore and verify the artifact writer's unmapped-state warning and graph_extras safety path. Evidence: missing import reproduced NameError red; graph_extras and analysis artifacts verified, focused 1/1 and full 124/124 PASS.
- [x] T024 Make artifact serialization strict, finite, deterministic and atomically persisted. Evidence: unknown object and nondeterministic set reproduced red; focused artifact 3/3 and full 124/124 PASS; existing artifact sentinels survive rejected values.
- [x] T025 Enforce one active analysis before destructive reset, clean failed initialization, and make run IDs collision-resistant. Evidence: overwrite, reset-before-claim and same-clock ID collision all reproduced red; lifecycle 4/4, full 129/129, prompt 26/26, compile/diff PASS; rejected run emits error then stream_end without calling reset.
- [x] T026 Validate RunnableConfig and workflow step definitions at their entry boundaries. Evidence: malformed configurable and empty workflow reproduced red; focused 2/2, full 131/131, prompt 26/26, compile/diff PASS.
- [x] T027 Cancel and await-collect all Step6 inflight tasks through one PauseController lifecycle boundary. Evidence: duplicate cancel-only loops and unregistered warmup reproduced red; focused 3/3, full 134/134, prompt 26/26, compile/diff PASS; pause retries after cleanup while external cancellation propagates.
- [x] T028 Make persisted graph edges and live NDJSON deltas isomorphic, fail closed on unsupported/invalid edges, and prove canvas selections exclude unrelated relationships. Evidence: Analyzer producer red 5/5 then focused 12/12 and full 140/140 PASS; NEXT/semantic graph parity and CALLS dedicated boundary complete. Byte-aligned standalone/pinned sources pass pure projection and targeted strict TypeScript checks. Actual UI Playwright verifies READS relationship chip, FUNCTION label chip, Navigator single-node→NVL 1-hop, and module interaction/collaboration area/health views with exact node and relationship inventories, unrelated 0 and console error 0 on both frontends.
