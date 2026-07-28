# Analysis: Evidence chain and impact

## Authoritative evidence

The scratchpad verifier was rerun on 2026-07-27 and passed:

- function ledger: 71 rows × 8 fields;
- schema ledger: 66 rows;
- evidence backfilled: 17 (`RULE` 6, `EXAMPLE` 6, `search_text` 5);
- decisive empty evidence: 0;
- fixed-gold source spans validated: 1,382;
- non-rejudged field and EXAMPLE aggregates unchanged.

The failure ledger contains 500 evidence-backed rows. High-risk phenomena include rule/example
omission, condition-direction reversal, negation/exception loss, boundary/value distortion,
early-exit/no-data loss, fallback loss, false questions, glossary hallucination, and schema meaning
errors.

## Run-110 stage findings

1. The original `code_summary` and `code_rules_examples` body are identical for 416/416 targets.
2. No compact-body marker occurs in the run, so current-body compression is ruled out for this run.
3. The final search/logical/stereotype values equal the structured response for all 71 representatives.
4. Historical final summaries equal the post-rewrite output for all 71 representatives.
5. The now-removed summary rewrite ran for 26/71, changed 23/71, and ran twice for 10/71.
6. `summary_rewrite` logs used `target_id=unknown` because the caller omitted `id` from `user_values`.
7. Four rules targets used fragments/merge; the merged final output is preserved.
8. Rule nodes are intentionally shared by statement hash. An unscoped audit query followed a shared
   Rule to all Examples and mixed 30 foreign Examples. Product `search/corpus/evidence_reader.py`
   already uses the correct `e.id STARTS WITH n.id + '::'` ownership constraint.

## Proven cause classification

| Stage | Finding | Classification |
|---|---|---|
| source → assembled prompt | original body present | no input omission for the inspected run |
| compression | 0 compact markers | no current-body compression loss for the inspected run |
| model response | many semantic omissions/extras already present | prompt/model generation defect |
| structured parser | scalar/rule atoms preserved | no proven parser loss for inspected records |
| removed summary rewrite | 23 changes, 10 repeated | a second semantic author can alter/erode content |
| merge | four targets, final preserved | no proven merge loss in inspected targets |
| Neo4j write | final fields match logged stage | no proven scalar write overwrite |
| audit read | 30 cross-function examples | ownership/read-scope defect in the old harness |
| run initialization | flat root is recursively deleted | prompt-evidence overwrite is deterministic |

## Codegraph/raw-source impact

- `normalize_searchable_text` feeds both framework per-node and DBMS batch analysis and now performs
  only whitespace normalization plus required-field validation.
- `coerce_stereotype` feeds both strategies and the graph stereotype-label writer.
- `run_yaml_rule` affects all primary semantic producers. No secondary rule named
  rewrite/repair/review/judge/merge/fallback remains.
- `run_executor` owns run identity, destructive graph initialization, runtime-log attachment, and
  output initialization.
- `llm.tracing` and YAML usage logging currently resolve a global flat output directory.
- CLI profile resolution must occur before `execute_run`, while Neo4j per-run selection can reuse the
  existing context-variable override.
- `search/corpus/evidence_reader.py` is the product reference for function-owned Example traversal.

## Generalized change decisions

1. Replace blanket “any number means policy” detection with structural condition/code-pair signals.
   Domain-defining sizes, identifiers, or protocol widths may be essential summary content.
2. Remove summary rewrite, rules fragment/merge fallback, reduced-summary fallback, and automatic
   semantic deduplication. A primary producer's first structured output is the only semantic output.
3. Reject missing required fields instead of copying, inferring, or auto-filling them. Do not choose a
   first candidate or truncate stored meaning.
4. Render stereotype options from the ontology source instead of duplicating lists in prompts.
5. Strengthen neutral prompt contracts with paired positive/negative examples. Do not introduce
   language, customer, corpus, function, table, or code-value branches.
6. Preserve default flat logs. When explicitly supplied, use the exact isolated run-output directory
   and write its manifest there without an implicit child or automatic backup.
7. Implement profile resolution as a pure reusable module; the CLI is only an adapter.

## Risks

- Prompt expansion can increase tokens. Shared contract fragments should replace duplicate prose.
- Over-aggressive deterministic semantic filters can discard grounded meaning. Deterministic gates are
  therefore limited to exact ownership/evidence/count/closed-label contracts and required-field
  validation; they do not author meaning.
- Explicit output selection affects tests and operator documentation. Default flat `logs` behavior is
  preserved; isolation is opt-in and exact.
- The dirty worktree contains unrelated step5/step7/step8 work. Edits must avoid those files unless
  an existing diff is fully understood and preserved.

## Source-coordinate and prompt-input audit

The pre-change product used one raw `code_text` string for incompatible responsibilities:

- step 2 graph persistence and source-detail display;
- deterministic call/literal/reference analysis;
- Framework and DBMS model input;
- glossary source evidence and search corpus text.

Framework branch coordinates were routine-relative while DBMS node keys were absolute ranges.
Framework source bodies had no line prefixes, DBMS batches showed only a range heading, and compacted
children did not share one machine-readable coordinate contract.

The generalized resolution is:

1. `SourceExcerpt` owns exact `raw_text`, absolute-line `numbered_text`, source range, status, and
   coordinate-system metadata.
2. Source-backed graph-node `code_text` is numbered; `raw_code_text` is retained only for in-run
   deterministic consumers and is not written as a Neo4j property.
3. Linkers, regex/literal extraction, context token measurement, domain-term validation, grounding,
   and glossary membership read preserved raw source. They never strip rendered prefixes.
4. Framework and DBMS source bodies, large-context summarization, external evidence, glossary source
   excerpts, graph source properties, and the source-detail API expose absolute-line coordinates.
5. The internal Framework control tree remains routine-relative for deterministic slicing, but prompt
   branch indexes and model-produced `anchor_line` use an explicit offset so the model sees only
   absolute file lines.
6. Prompt suffixes separate `TARGET`, `DIRECT_EVIDENCE`, `SURROUNDING_CONTEXT`,
   `CALLEE_INTERFACE`, and `SOURCE`. Module intent, codebase notes, ancestor skeletons, and callee
   summaries are explicitly non-target evidence.
7. Compact child placeholders retain `child_range` and identify themselves as context summaries, not
   executable source. The renderer uses one compact `N | text` prefix rather than per-line JSON.

The remaining model inputs were audited. Module pre-analysis now receives member identity, signature,
label, and source range as compact JSON; module synthesis receives member ownership/range metadata;
table/component prompts carry semantic records rather than source bodies.

## Direct-access and repetition audit

The table prompts instructed the model to reserve `upsert` for authoritative MERGE evidence, but that
evidence was absent from the rendered request, and the DBMS fan-in omitted it from the existing
deterministic validator. Both strategies now receive the same per-table single-operation map. The
validator preserves the LLM-owned subtype (`lookup` versus `READ-only`, `append-only` versus
`WRITE-only`) but rejects an entire insight when its read/write direction contradicts direct evidence;
it never rewrites `role` or `usage_summary`.

The previous per-rule `examples` ceiling of 80 was based on function-wide counts. The official
per-rule distribution was p50=1, p90=2, p95=3, p99=6, max=19. The schema and quality detector now
share a ceiling of 24: above the observed maximum with margin, but low enough to stop structural
repetition before an 80-item response.

## Post-rerun cause audit additions

- `branch_index()` inferred complement branches from `len(atoms(condition)) <= 1`. This converted
  ordinary single-atom IF/CASE decisions and SWITCH controllers into the same “other” text. The fix
  is structural role classification, not a larger token threshold.
- Rule nodes are intentionally shared by normalized statement, but HAS_RULE and Example IDs lacked
  a routine-local occurrence key. Repeated identical statements therefore collapsed three Rule
  occurrences and five Examples in the audited run. Occurrence identity belongs on the relation and
  in Example IDs; the shared Rule meaning identity remains unchanged.
- A `(source, table, READS|WRITES)` relationship retained only the last scalar evidence coordinate.
  Nineteen audited edges had multiple real access lines. The universal contract is a sorted unique
  location collection, with Catalog expanding locations for independent source links.
- Glossary alias candidates used normalized substring containment. That admitted table names,
  operation keys, functions, and I/O types into false synonym groups. Candidate generation must use
  compatible typed provenance and positive equivalence evidence; related-but-distinct entities stay
  separate.
- Per-routine table insight input included every DDL column, producing hundreds of unsupported
  column candidates. Direct analysis must see only directly referenced columns; the table-level
  synthesis remains responsible for the complete DDL set and exact final count.

## R/W source-highlight audit

The live SKT graph contains 85 `READS`/`WRITES` relationships. All 85 have a non-empty
`evidence_file` and positive `evidence_line`; all coordinates fall within their source node range and
refer to the same file. Catalog exposes the relationship line for Framework references and the
frontend uses the absolute value as both the displayed line and source-panel highlight coordinate.

An exact source check found 3/85 relationship coordinates on commented-out C calls. The coordinate
transport was correct, but the regex linker had scanned raw comments as active code. The linker now
uses the shared language lexicon to remove comments and inactive blocks before matching while
preserving line layout and executable quoted strings. This is language/data-driven, not a
project/function/table exception. A separate downstream impact remains documented: Catalog's DBMS
reference query still names legacy `FROM|WRITES` while Analyzer emits `READS|WRITES`; it is outside
this Analyzer repository change and must be aligned before claiming DBMS frontend parity.

## Three-split strategy audit

Framework and DBMS share three semantic concerns but intentionally do not share an execution unit.
Framework owns one routine per call. DBMS owns a token-bounded control-subtree unit and may send
several sibling units in one flat keyed request. The shared contract therefore belongs at the
concern-result boundary, not in one combined strategy engine.

The current DBMS fan-in converts output lists directly to dictionaries. Duplicate keys silently
overwrite earlier values, unknown keys disappear, and only missing Summary items trigger a focused
request. That focused request invokes all applicable concerns again, discarding an already successful
Rule/Example or Table Insight for the same node. Split failures are logged but are not attached to the
node as `partial`, unlike Framework.

The generalized correction is:

1. each concern receives one initial attempt and at most one focused retry;
2. a successful concern result is immutable and never called again;
3. Framework retries the failed concern for that one routine;
4. DBMS preserves uniquely keyed successes and retries only unresolved keys for the same concern;
5. a concern that does not apply, such as Table Insight without direct R/W, is neither called nor
   marked failed;
6. duplicate, unknown, missing, parse, and transport failures are explicit;
7. exhausted failures leave the original semantic fields empty and add the concern to `partial`;
8. no retry may use a sibling field, reduced prompt, repair prompt, merge, or deterministic meaning.
