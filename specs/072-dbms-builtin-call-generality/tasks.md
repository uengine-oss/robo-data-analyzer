# Tasks

- [x] T001 Add red project-prefix, exact-package, and lookalike call tests.
- [x] T002 Add a red DBMS strategy step-membership contract.
- [x] T003 Implement the data-owned exact-package policy.
- [x] T004 Run focused and authoritative gates.
- [x] T005 Record live RWIS call/self-loop evidence.

## Evidence

- Red: prefix-collision cases failed under the former prefix filter; Green:
  focused call/pipeline contracts passed 4/4.
- Authoritative Analyzer gates: structure 18/18, full 180/180, prompt
  byte-identical 26/26, compileall and diff-check passed.
- Live RWIS graph: CALLS 5 (internal 2, external 3), built-in CALLS 0,
  self-loops 0. Independent answer-key CALL comparison mismatch 0.
- DBMS run log contains `link_dbms_tables_step` completion and zero
  `link_deterministic_step` events; the declaration contract pins the no-op.
- Final evidence: `D:/work/robo/goal-verify/output/reports/diff-report-rwis.json`
  and `rwis-stream-graph-grounded.json`; the live run completed with DBMS
  CALLS self-loops 0 and answer-key relation mismatches 0.
