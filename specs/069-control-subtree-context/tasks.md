# Tasks

- [x] T001 Add red relative-coordinate, nesting, and context-wiring tests.
- [x] T002 Add ontology-derived control label policy.
- [x] T003 Build and attach routine-owned `control_subtree` values.
- [x] T004 Prove anomalous rules execute a non-empty fragment path.
- [x] T005 Run focused, structure, full unittest, prompt, compile, and diff gates.
- [x] T006 Update the audit ledger and superseded spec 054 T013 evidence.

## Evidence

- Red: focused module import failed because the production subtree builder did
  not exist.
- Green: context/subtree/fallback focused 11/11; reader+CASE+context+fallback
  integration 13/13.
- Authoritative: structure 18/18, full unittest 168/168, prompt byte identity
  26/26, compileall and `git diff --check` passed.
