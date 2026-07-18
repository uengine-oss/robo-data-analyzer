# Tasks

- [x] T001 Add red AST-loading stop and pause/resume contracts.
- [x] T002 Make `PauseController.gate` yield before observing state.
- [x] T003 Add bounded cooperative checkpoints throughout step2 loading.
- [x] T004 Run focused and authoritative regression gates.
- [x] T005 Verify live AST-loading stop reaches `stopped` and `stream_end` promptly.
- [x] T006 Update audit-ledger and D-5 evidence.

## Evidence

- Red: both initial contracts failed; stop read both AST files and returned,
  while pause did not hold the step.
- Green: file stop, pause/resume, reference-link checkpoint, and existing
  control contracts passed 8/8.
- The first live attempt exposed the long synchronous `link_references` region
  before AST events; the cooperative callback was extended through that loop.
- Live rerun on E2E database `test`: stop sent 50 ms after
  `parse_ddl_step` completion while step2 was active; `stopped` in 0.008241 s,
  `stream_end` in 0.009400 s, error events 0. Evidence:
  `goal-verify/output/goal-d/d5-ast-stop-live/result.json`.
- Authoritative: structure 18/18, full unittest 175/175, prompt byte identity
  26/26, compileall and `git diff --check` passed.
