# Implementation Plan

1. Generalize the existing exact one-line CALL renderer; do not add a second path.
2. Remove semantic-evidence gating from call-site syntax resolution while retaining exact
   parser/source callee comparison.
3. Add internal-callee and mismatch regression tests.
4. Run unit/full tests, preflight, isolated RWIS rerun, and graph/gold adjudication.

## Constitution Check

- I: a statically named exact call is deterministic, so LLM use is a defect.
- III/VIII: the existing leaf resolver and summary apply path remain single owners.
- IV: unprovable calls still fail closed to the semantic path.
- IX/X: change starts from two exact rejected RWIS coordinates and their actual prompts.

