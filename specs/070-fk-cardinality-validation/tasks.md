# Tasks

- [x] T001 Add red FK cardinality parser tests.
- [x] T002 Derive declared unique-key sets and assign explicit FK properties.
- [x] T003 Compare FK column identity and properties in the independent validator.
- [x] T004 Prove property mismatches make validation fail.
- [x] T005 Run Analyzer and goal-validator authoritative gates.
- [x] T006 Run a current-product live source-to-graph FK comparison.
- [x] T007 Update audit-ledger evidence.

## Evidence

- Red: unique and composite-unique cases failed 2/4 because all FK edges used
  the dataclass `many_to_one` default.
- Green: parser focused 4/4; DDL namespace integration 1/1; FK validator 3/3;
  relation fixture outcomes 6/6; reset policy 5/5.
- Authoritative: Analyzer structure 18/18, full unittest 172/172, prompt byte
  identity 26/26, compileall and `git diff --check` passed.
- Current product parsed and upserted 11 declared Shopmall FKs with explicit
  properties; independent live diff then reported tables 22/22 and FK 11/11,
  missing 0, extra 0, miswired 0, property mismatch 0.
