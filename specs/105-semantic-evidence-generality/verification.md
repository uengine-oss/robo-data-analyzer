# Verification evidence

**Date**: 2026-07-27 KST  
**Official corpus reanalysis**: pending generalized fixes and regression gates

## Fixed evidence verification

The scratchpad verifier was rerun before the explicitly authorized source-comment correction:

```text
fixed source files: 23
fixed LF count: 44,142
routines: 416
claims/source spans: 1,382
function ledger: 71 rows × 8 fields
schema ledger: 66 rows
evidence backfilled: 17
decisive empty evidence: 0
non-target aggregates unchanged: true
```

The failure ledger was regenerated with 500 evidence-backed rows and zero decisive empty evidence.
The run-110 cause matrix was regenerated:

```text
initial summary/rules body equal: 416/416
compact markers: 0
summary rewrite invoked: 26/71
summary changed: 23/71
summary rewritten twice: 10/71
scoped RULE/EXAMPLE/QUESTION exact: 416/416
unscoped Example-contaminated functions: 21
cross-function Examples in old unscoped audit read: 30
```

Historical prompt logs and adjudication assets remain in the existing scratchpad, not this repository.
The fixed gold remains unchanged. The later comment-only source correction changed 19 of 23 files and
340 comment lines; scratchpad backup, line-count equality, and executable-code hash equality hold for
23/23 files.

## Implemented generalized contracts

- summary post-processing only strips surrounding whitespace and rejects empty required
  `summary`/`search_text`; it performs no rewrite, inference, copy, or truncation;
- the summary-rewrite, reduced-summary retry, rules fragment/merge fallback, and deterministic
  semantic-deduplication paths were removed;
- stored semantic fields are not populated from sibling generated fields, first candidates, or
  automatic empty-value fillers; the explicit source `description` ← `analyzed_description` table
  contract remains;
- `search_text` has one shared all-language/all-domain contract with paired good/bad examples;
- RULE/EXAMPLE/QUESTION high-risk evidence rules have one shared contract with paired good/bad examples;
- response locale is carried in run context and injected into both framework and DBMS prompts;
- stereotype prompt options and parser validation use the same ontology source;
- framework ontology includes general `Router`, `Utility`, and low-level `Util` roles without any
  project/corpus branch;
- glossary synthesis requires non-empty evidence that is a substring of supplied context;
- YAML-rule evidence records both raw and parsed responses, including cache-hit provenance;
- default execution keeps the established flat `logs` behavior; an explicit run output is used
  exactly and receives the pre-mutation manifest without an implicit child or automatic backup;
- profile resolver implements explicit CLI > profile > environment/default precedence without
  moving data or editing `.env`.

## Codegraph analyze

Command entrypoint used:

```powershell
& "$env:APPDATA\npm\codegraph.cmd" sync
& "$env:APPDATA\npm\codegraph.cmd" status
```

Result:

```text
files: 422
nodes: 4,941
edges: 9,481
index: up to date
```

Impacts inspected after implementation:

- `execute_run`: API and CLI callers plus HTTP boundary tests;
- `run_yaml_rule`: primary routine, module, table, component, and glossary producers; no secondary
  rewrite/repair/review/judge/merge/fallback semantic producer remains;
- `get_run_dir`: tracing, usage, health report, and downstream callers;
- `AnalysisRun`: executor and immutable-output/lifecycle tests;
- `stereotype_values`: prompt loader, runner, and semantic-generality tests;
- `_accept`: glossary step and grounding tests.

## Tests

Targeted semantic/profile/output/ownership/LLM suites:

```text
Affected semantic/profile/output suites passed at their checkpoints.
```

Full Analyzer suite, with repository root declared as unittest top-level:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py" -t .
```

Result:

```text
Ran 477 tests
OK (skipped=5)
```

Additional checks:

```text
compileall pipeline/llm/scripts/tests: PASS
prompt byte-identical baseline: PASS (31 prompts)
```

`git diff --check` over the whole dirty worktree reports pre-existing trailing whitespace in unrelated
spec 078/081 research files. Those user-owned changes were preserved and not normalized by this work.

## Generality audit

- No observed corpus/customer identifiers remain in product prompts.
- No changed semantic decision branch checks customer, project, corpus, industry, function, table, or
  code value.
- Java, Python, C-like, and SQL fixtures render the same shared contracts.
- Natural-language keyword detection was removed from deterministic summary logic; it does not grow a
  per-language special-case table.
- Prompt material uses paired `잘못/좋음` examples.

## Preserved external state

- No official Analyzer run had been started at this verification checkpoint.
- No Neo4j database was wiped or rewritten by this verification.
- Fixed gold was not moved, deleted, or edited. Analysis source received only the explicitly
  authorized comment corrections described above; executable code and line counts are invariant.
- Scratch adjudication harnesses were not copied into the Analyzer repository.
- No Git staging or commit was performed.
- Pre-existing dirty WIP remains present.

## Source-coordinate contract verification

New contract coverage proves:

- exact raw text and numbered text preserve the same selected lines, including blank lines;
- step 2 attaches numbered public `code_text` and exact internal `raw_code_text`;
- a routine beginning after line 1 presents the same absolute line in source, branch index, and
  grounding lookup;
- a compacted DBMS child is one explicit `CONTEXT_SUMMARY` placeholder carrying its absolute range;
- Framework and DBMS rendered prompts identify direct evidence, surrounding context, target/source
  ownership, and the `absolute-file-line` coordinate system;
- linked external source evidence and glossary excerpts are line numbered;
- deterministic linkers and grounding continue to consume raw source.
- Framework and DBMS table prompts carry deterministic table operations; closed table roles that
  contradict direct R/W evidence are rejected whole, while LLM-owned usage text is never rewritten.
- `RuleItem.examples` and the quality detector share the evidence-derived ceiling of 24 (observed
  per-rule maximum 19), replacing the earlier function-count-derived ceiling of 80.
- Live Neo4j R/W coordinates are complete and internally consistent for 85/85 relationships. Exact
  source inspection exposed 3 commented-call false positives; active-source lexical masking now
  removes those matches without changing physical line coordinates or quoted executable SQL.

Commands:

```powershell
.\.venv\Scripts\python.exe -m compileall -q shared graph pipeline tests
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py" -t .
.\.venv\Scripts\python.exe scripts\check_byte_identical.py
```

Result:

```text
compileall: PASS
unittest: 481/481 PASS (5 skipped)
prompt hashes: 31/31 PASS
```

`git diff --check` still reports only pre-existing trailing whitespace in unrelated spec 078/081
research files. No Analyzer source, fixed gold, Neo4j data, staging area, or commit was mutated by
verification.

## Official-run prompt evidence interruption and correction

The first correctly detached official run exposed one `code_summary` result rejection while 416
routine analyses were running concurrently:

```text
PermissionError: [WinError 5] ... code_summary.json.tmp -> code_summary.json
```

This was not a model, parser, or corpus failure. The aggregate evidence writer used one fixed
temporary filename and made a single Windows replacement attempt. A transient sharing denial from a
reader or filesystem scanner therefore discarded an otherwise completed result, correctly causing
that routine to be skipped rather than accepting evidence-free semantics.

The shared writer now creates a unique temporary file, closes it before replacement, retries only
transient `PermissionError` with a small bounded backoff, and removes every leftover temporary file.
Contract tests inject the observed first-attempt denial and write 64 records concurrently, asserting
valid JSON, 64/64 unique target records, and zero temporary-file residue.

## Three-split concern-retry verification

Framework and DBMS retain their different execution units:

- Framework retries only the failed concern for one routine.
- DBMS preserves every uniquely keyed success and retries only unresolved keys for that same concern
  inside its control-subtree batch.
- A successful Summary, Rule/Example, or Table Insight is never regenerated, merged, rewritten, or
  replaced by a sibling field.
- Non-applicable Table Insight work is not called and is not marked failed.
- Duplicate, unknown, and missing DBMS keys are rejected explicitly. A retry-exhausted concern stays
  empty and is transported as `analysis_partial` without authoring substitute meaning.

Codegraph synchronized 432 files, 5,117 nodes, and 9,622 edges. Impact inspection confines the shared
retry helper to the Framework fan-out, DBMS keyed collector, their callers, and focused tests; the
durable partial-state change reaches only `CodeNode` summary persistence and graph-delta projection.

Result after the concern-retry implementation:

```text
compileall: PASS
unittest: 505/505 PASS (5 skipped)
prompt hashes: 31/31 PASS
```

No official reanalysis, Neo4j reset, Git staging, or commit was performed. Existing dirty WIP and the
fixed analysis gold/source corpus remain untouched.

## Second-rerun deep preflight

Rerun-1 failures were re-opened from the fixed ledger and actual `llm-prompts` requests/responses,
then checked against active source instead of treating the judge as an oracle. Two alleged Rule
hallucinations were verifier defects:

- `Check_Sum` copies `LEN_ONE=1` and therefore does process one character per iteration.
- `ordf_get_ordr_numb` explicitly adds 300,000 at active source lines 819-823; the fixed gold
  `source_span` begins at 830 and caused the previous judge to miss valid evidence.

Real residuals were same-effect value grouping, no-data/error separation, termination-before-write
ordering, answerable Question generation, and role boundaries. The shared Rule contract now follows
active execution order and the Framework stereotype prompt uses a final-observable-responsibility
decision procedure with paired counterexamples. Parsed DDL tables remain the table synthesis target
even without code insight. A table synthesis transport/schema/repetition failure now receives one
identical-request retry; it never rewrites, merges, copies, or substitutes meaning.

Rerun-1 prompt audit:

```text
summary:       418 records, 416 targets, absolute SOURCE range mismatches 0
rules:         417 records, 416 targets, absolute SOURCE range mismatches 0
table insight:  78 records,  78 targets, absolute SOURCE range mismatches 0
successful raw JSON -> parsed mismatches: 0
```

The extra Summary/Rule records are bounded retries. Cache-hit records legitimately have no raw
message but carry validated parsed output; no successful raw/parsed pair disagreed.

Preflight gates:

```text
Codegraph: 432 files, 5,140 nodes, 9,693 edges; index up to date
compileall: PASS
unittest: 518/518 PASS (5 skipped)
prompt hashes: 31/31 PASS
```

The original corpus and fixed gold were read only. No staging, commit, or official second rerun had
occurred at this checkpoint.

## Official rerun-2 final and post-run hardening

Official execution:

```text
profile: D:\work\robo\_runs\analyzer-skt-20260728-rerun2-final-profile.yaml
output:  D:\work\robo\_runs\analyzer-skt-20260728-rerun2-final
run_id:  20260728_055755_602144ce91db
elapsed: 778.9 seconds
functions: 416/416
summary/search/logical: 415/416
rules/examples/questions: 1,902 / 2,331 / 4
tables/columns: 7/7 / 73/73
```

The same 23 source hashes and the same fixed gold correction hash were used. Prompt evidence contains
426 Summary records, 419 Rule records, 78 Table Insight records, 22 Module records, and 7 Table
Description records. Successful raw JSON and parsed values disagree in 0 records. The one exhausted
Summary concern is `code:zapamcom10020.c:Bank_039_CSB_Check`; no sibling-field copy or semantic rewrite
was used.

The first strict judge output contained provable false negatives, including eleven existing Rules for
`b450_mq_trns` being treated as absent. Direct source/gold/output-ID review corrected nine verdicts.
For the fixed 20-function active population:

```text
active gold claims: 219 (dead_code 8 excluded)
Rule Coverage:    157/219 (71.7%) -> 191/219 (87.2%)
Example Coverage: 156/219 (71.2%) -> 191/219 (87.2%)
complete functions: 4/20 -> 10/20
confirmed active contradictions: 0
```

Full write and coordinate audit:

```text
function semantic anchors: 416/416 present
HAS_RULE: 1,902/1,902; statement mismatches 0
HAS_EXAMPLE/EXAMPLE: 2,331/2,331; text mismatches 0
questions/modules/tables/columns/components: mismatches 0
numbered code_text: 4,484/4,484
READS/WRITES evidence: 84/84; bad lines 0
```

The run exposed a residual bookkeeping defect: reapplying all patterns replaced the original 205
physical unresolved sites with 410 pattern-derived unresolved rows. The product now keeps the
original physical-site population and removes only sites with newly evidenced R/W edges. A missing
member Summary no longer suppresses an entire Module Summary; only verified sibling member summaries
are aggregated, without substituting another semantic field. Two source-description violations in
column prose led to a universal source-semantic-head/unknown-abbreviation paired-example contract.
No further full rerun was executed after these post-run fixes.

Final gates:

```text
compileall: PASS
unittest: 525/525 PASS (5 skipped)
prompt hashes: 31/31 PASS
Codegraph: 432 files, 5,154 nodes, 9,784 edges; up to date
```

## Rerun-2 residual direct audit and prompt consolidation

The remaining 28 active machine-missing claims were re-read against their fixed evidence spans and
the stored first Rule/Example response. Three validation claims in
`code:zngmmcap50000.c:a000_input_validation` were verifier false negatives: separate generated Rules
already state the all-alternatives-absent rejection, the per-operation required values, and the final
success path. Character equality was not required. This corrects same-run active Rule and Example
Coverage from 191/219 (87.2%) to 194/219 (88.6%); it is a verdict correction, not a product
improvement.

One claimed miss in `code:apascrtu0010t02.c:c010_rltm_auth_proc` also exposed an over-broad generated
Rule: the response says every unsent-notification path invokes delivery, while active source has a
nested device-condition path that deliberately skips it. That item remains non-entailed and is now
also one confirmed contradiction. The residual ledger is therefore 24 genuine omissions, one
omission-plus-contradiction, and three verifier false negatives. The earlier statement of zero active
contradictions is superseded by this closer audit.

The genuine omissions occur in the first authoritative Rule response, not parser, fan-in, merge, or
Neo4j write. They cluster in three universal mechanisms:

- independent branches compressed into a phase-summary Rule;
- output keys/messages/fixed records mistaken for disposable buffer work even though they flow to
  return, storage, lookup, or external-call inputs;
- positive alternatives, unchanged pass-through, or failure-with-continue omitted.

The shared Rule contract now owns one ordered decision procedure for those mechanisms with unrelated
neutral good/bad examples. Framework and DBMS wrappers retain only strategy-specific target,
ownership, anchor, table-operation, and node-key rules instead of repeating the shared semantic
policy. Framework `branch_index` is the final Rule-specific volatile input after child context.
No deterministic meaning author, second semantic LLM, rewrite, merge, sibling copy, empty fill, or
truncation path was added.

Post-change gates:

```text
focused semantic/residual/postprocess suite: 98/98 PASS
full unittest: 526/526 PASS (5 skipped)
compileall: PASS
prompt hashes: 31/31 PASS
changed Rule prompts: observed corpus identifiers 0
git diff --check on changed prompt/spec/test slice: PASS
```

No new CLI analysis or Neo4j reset was performed. Therefore 88.6% is the corrected score of the
preserved rerun-2 output, while the semantic effect of the new prompt consolidation is unmeasured.
