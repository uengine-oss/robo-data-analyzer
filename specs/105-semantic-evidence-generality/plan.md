# Implementation Plan

## Constitution alignment

- Single truth: ontology vocabulary, prompt semantic contract, profile resolver, and run-output
  resolver each have one owner.
- Strategy-neutral workflow: framework/DBMS consume shared helpers; no engine-name behavior branch is
  added to common code.
- No silent failure: invalid profiles, unsafe output paths, missing required fields, and missing evidence
  are explicit.
- Schema restraint: one flat profile schema and one flat manifest; no unbounded nested output.
- Preserve user work: no reset/stage/commit, no corpus mutation, no harness files in the product repo.

## Phase 1 — Contracts

1. Add the analysis-profile schema and precedence contract.
2. Add the immutable run-manifest contract.
3. Centralize stereotype prompt vocabulary and semantic prompt fragments.

## Phase 2 — Implementation

1. Keep the default flat logs location and use an explicitly supplied run-output directory exactly.
2. Reset only the selected output directory; do not invent a run-ID child or automatic backup.
3. Add run-output context used by tracing and usage writers.
4. Write a prompt-tree hash manifest before Neo4j wipe/LLM invocation.
5. Add a pure profile loader/resolver/preflight shared by CLI and CI.
6. Add CLI flags for profile, database, locale, and run output with explicit-argument precedence.
7. Remove secondary semantic rewrite/repair/merge/fallback and automatic semantic mutation; keep
   validation observational or reject-only.
8. Render the closed stereotype vocabulary from one source.
9. Filter module-summary members through the active strategy's existing analysis-target contract.
10. Add a shared source-excerpt contract that preserves exact raw text and renders a token-efficient
    absolute-line-numbered representation for node properties, prompts, logs, and source APIs.
11. Migrate deterministic code consumers to the raw representation and prohibit prefix stripping.
12. Give Framework and DBMS prompts the same structured evidence envelope and absolute line
    coordinates for source, branch indexes, anchors, ancestor context, and compact placeholders.
13. Classify control entries as controller, decision, or complement from parser/ontology facts;
    never infer complement from text length or token count.
14. Preserve function-local Rule occurrences and all R/W evidence coordinates without duplicating
    shared semantic nodes.
15. Restrict glossary aliases to compatible typed provenance and split per-routine direct column
    evidence from table-level complete DDL synthesis.
16. Preserve the strategy boundary while enforcing one shared three-split failure contract:
    Framework retries only the failed per-function concern; DBMS retries only missing or failed
    `node_key` items for that concern. A successful Summary, Rule/Example, or Table Insight is never
    regenerated, merged, repaired, or replaced.
17. Validate DBMS batch output keys against the exact requested set. Preserve uniquely matched
    successes, retry only missing/duplicate items once, reject unknown keys explicitly, and attach
    final concern failures to the affected node's `partial` metadata.

## Phase 3 — Tests

1. Default-flat and explicit-exact output/manifest tests.
2. Profile precedence, safety, and no-LLM-before-preflight tests.
3. Multilanguage/multidomain semantic contract fixtures.
4. Good/bad example and forbidden-specialization static prompt checks.
5. Shared Rule/owned Example traversal regression.
6. Affected unit suites, compile/import smoke, and repository diff checks.
7. Static checks for cross-field copying, first-candidate substitution, empty-value auto-fill, and
   stored semantic truncation.
8. Multilanguage source-excerpt tests covering first-line offsets, whole-file ranges, blank lines,
   compact child ranges, branch-anchor conversion, and raw-text preservation.
9. Prompt-envelope tests proving that direct evidence, surrounding context, callee interfaces, and
   source are distinct and that no model-visible source body lacks line numbers.
10. Branch-role fixtures for C, Java, Python, and SQL; duplicate-Rule occurrence cardinality;
    multi-location R/W transport through Catalog; typed glossary positive/negative aliases; and
    direct-column-subset versus complete-DDL tests.
11. Three-split retry tests for both strategies: success is called once, only the failed concern is
    called twice, non-applicable Table Insight is never called, DBMS unique successes survive a
    sibling miss, duplicate/unknown keys are explicit, and exhausted failures remain `partial`
    without cross-field recovery.

## Phase 4 — Analyze and verify

1. Compare implemented files against Codegraph impact evidence.
2. Search changed product files for customer/corpus/domain special cases and duplicated truth.
3. Record commands, results, limitations, and next official rerun/delta procedure.

## Phase 5 — Official rerun and same-gold delta

1. Finish all prompt-conflict, cause-to-fix, field-copy, Codegraph, and local regression audits.
2. Preflight the explicit SKT profile and confirm the Analyzer-owned disposable Neo4j database.
3. Execute the real CLI once with an explicitly isolated output directory so its manifest,
   raw requests/responses, parsed records, and usage evidence are not mixed with the default logs.
4. Prove graph topology, ownership, and cardinality at 100%, then adjudicate natural-language fields
   against the unchanged gold by direct source/gold/output review with separate coverage and
   faithfulness scores; scripts only total the agent-authored verdicts.

## Phase 6 — One-rerun residual hardening

1. Derive table-description targets from parsed DDL tables rather than optional function insights;
   pass source metadata, complete owned columns, and deterministic R/W evidence to the existing single
   table-level LLM producer.
2. Require positive absolute source anchors for conditional and unconditional Rule occurrences.
3. Reject exact same-anchor/same-scenario Rule duplicates in both Framework per-node and DBMS batch
   response items while preserving equal meanings at different source occurrences.
4. Refine the shared stereotype decision rubric around final observable responsibility, using only
   neutral paired good/bad examples.
5. Add multilingual/multidomain contracts for all four mechanisms, run Codegraph/static impact
   checks, then run affected and full regressions. Do not launch another full corpus analysis.

## Phase 7 — User-authorized second official rerun

1. Reopen every rerun-1 failure family against the actual request/response records and verify the
   current fix at input, prompt, schema, fan-in, write, and verifier boundaries.
2. Strengthen only incomplete universal contracts; do not add corpus literals, name rules, semantic
   fallback, rewrite, merge, or post-hoc repair.
3. Run prompt rendering/hash checks, adversarial multilingual/multidomain contracts, Codegraph impact,
   compileall, and the complete unittest suite.
4. Create one new exact isolated run-output, preflight source/DDL/strategy/database/locale/output and
   prompt-tree identity, then execute the real CLI exactly once.
5. Verify source hashes and graph integrity first. Only after those gates pass, directly adjudicate
   the same representative source/gold/output fields and report absolute scores and fair deltas,
   including regressions and unproven areas.

## Phase 8 — Rerun-2 residual prompt consolidation

1. Directly re-read every active missing claim against source, fixed gold, and the stored first Rule
   response; separate verifier false negatives, genuine omissions, and contradictions.
2. Keep semantic authorship in the existing first Rule producer. Concentrate the confirmed universal
   decision procedure in the shared contract: independent-path inventory, outbound dataflow,
   positive alternatives, unchanged pass-through, and failure-with-continue.
3. Remove repeated semantic policy from Framework/DBMS wrappers while retaining their different
   target, ownership, anchor, and table-operation contracts.
4. Put Framework `branch_index` last among Rule-specific volatile evidence so the final generation
   checklist is not followed by lower-priority child context.
5. Prove shared-contract identity, neutral paired examples, volatile-input ordering, prompt hashes,
   specialization absence, and full regression. Do not claim measured model improvement without a
   new official analysis.
