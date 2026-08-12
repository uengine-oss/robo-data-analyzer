# Feature Specification: DBMS call-site deterministic truth

**Status**: In progress  
**Created**: 2026-08-11

## Problem

The isolated RWIS run produced two rejected summaries at the exact internal CALL sites
`PRC_DATA_ANALYSYS_TEST2.sql:227` and
`PRC_INSERT_WATERSISUL_MINUTE_2.sql:290`.  The actual prompt already contained the callee
summary, but the model copied that callee responsibility as the CALL node's own summary.
The grader correctly rejected it.  The syntax itself completely proves the narrower
call-site fact, so asking an LLM to restate it adds randomness without adding truth.

## Contract

1. A one-line PL/SQL `CALL` whose source callee exactly equals parser `node.name` is
   summarized deterministically for both internal and external callees.
2. The summary preserves the exact callee and argument expression.  It does not claim the
   callee's business result or runtime success.
3. Callee canonical summaries remain separate dependency evidence for parent/routine
   analysis; they are not copied into the CALL node summary.
4. Multiline, dynamic, or source/name-mismatched calls remain unresolved for the semantic
   path rather than guessed.
5. The existing summary apply/storage path remains the sole consumer path.

## Acceptance

- The two sealed RWIS internal CALL coordinates are deterministic and no longer create
  summary/module-summary findings.
- Existing external CALL, DBMS_OUTPUT, and fallback tests remain green.
- Full tests, prompt byte identity, preflight, and one isolated DBMS rerun pass.

