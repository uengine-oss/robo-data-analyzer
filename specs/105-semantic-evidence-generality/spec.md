# Feature Specification: Semantic Evidence Generality and Immutable Runs

**Feature Branch**: existing `main` dirty worktree (no branch creation)  
**Created**: 2026-07-27  
**Status**: In progress  
**Input**: Fixed semantic-gold evidence, representative verdict ledgers, restored run-110 prompt/response logs, current Analyzer code and Codegraph.

## Problem

The Analyzer produces useful semantic fields, but the fixed representative evidence proves that some
outputs omit decisive behavior, introduce unsupported meaning, or lose exact closed-label identity.
The same investigation also proved that the current flat `logs/llm-prompts` directory is destroyed at
the start of the next run, so evidence from one corpus can be overwritten by another run.

The observed C corpus and its project names are historical evidence only. The product solution must
work for every supported project, programming language, natural language, and business domain.

## Non-negotiable generality contract

1. Product behavior MUST NOT branch on a project name, customer, industry, programming language,
   function name, table/column name, or corpus-specific literal to improve semantic quality.
2. Prompts MUST express language- and domain-neutral contracts. Every material instruction MUST pair a
   good example with a bad example that uses neutral placeholders or unrelated generic domains.
3. Natural-language outputs (`summary`, `search_text`, rules, examples, questions, descriptions,
   glossary meanings) are accepted by semantic equivalence or sufficient similarity, not character
   equality.
4. Graph node/relationship topology and cardinality are the only quality result that requires a 100%
   match. Closed-label values must come from the exact ontology, while their semantic classification
   accuracy is measured at the same 80–90% target as other meaning fields.
5. Corpus-specific names and values may appear only in immutable historical-evidence documents, never
   in product decisions, product prompts, or general contract fixtures.

## User scenarios and acceptance criteria

### US1 — A user can trust the meaning of a routine

Given source in any supported language and domain, the Analyzer describes what the routine does,
preserves decisive direction/negation/exception/boundary/normal-error behavior, and does not invent
facts absent from the supplied evidence.

Acceptance:

- `summary` is a user-facing explanation of the routine's purpose, material action, and observable
  result; it is not reduced merely because it contains a domain-defining number.
- Every routine selected for semantic analysis receives a grounded `search_text`, including
  delegation, transformation, validation, I/O, accessors, empty implementations, and bodyless
  declarations. A bodyless unit describes that observable structural fact instead of letting the
  model make the ambiguous decision that it has "no meaning".
- The first structured field-generation response is the stored semantic truth. Product code performs
  no LLM rewrite/review/repair/merge of an existing field and no semantic fallback after it.
- Product code never copies another semantic field, picks a first candidate as a substitute, fills a
  missing meaning automatically, or shortens/truncates stored semantic content.
- Deterministic checks may reject an empty required field or enforce evidence, ownership, count, and
  closed-ontology membership, but may not create or alter meaning.
- Context-only summarization is the sole extra-LLM exception. It may compress prompt input but is
  never written back as a generated semantic field.

### US2 — Closed stereotypes have one exact vocabulary

The model sees the same strategy vocabulary that the parser validates. Framework and DBMS prompts do
not maintain handwritten copies that can drift from the ontology.

Acceptance:

- One product source defines the allowed stereotype values.
- Prompt rendering and `coerce_stereotype` consume that same source.
- Unknown labels fail closed to empty; known labels remain byte-exact.
- No alias is chosen by inspecting a project/domain/function name.

### US3 — Rule/example/question and glossary evidence remains owned and grounded

Shared Rule nodes may be deduplicated, but child Examples are read only for their owning function.
Grounded post-processing rejects semantic claims whose cited evidence is absent from the supplied
context.

Acceptance:

- Every function-scoped Rule→Example traversal filters the Example ID by the function prefix.
- Product readers and tests prove that a shared Rule cannot leak another function's Example.
- Glossary synthesis cannot promote a meaning whose evidence is blank or absent from its supplied
  context.
- Prompt guidance covers condition direction, negation/exception, early exit/no-data/error, boundary,
  fallback, dead/commented code, normal/error returns, false questions, and unsupported extras with
  paired good/bad examples.

### US4 — Default logs remain compatible and explicit output is operator-controlled

An ordinary analysis keeps the established flat `logs` behavior. An operator who needs an isolated
evidence set supplies an explicit output directory, and that exact directory is used without adding
an implicit run-ID child or automatic backup policy.

Acceptance:

- Omitting the run-output option resets and updates the established default `logs` directory.
- Supplying run-output writes runtime logs, usage records, raw/parsed prompt records, and the manifest
  under that exact directory.
- Product code does not silently relocate the output, add an implicit child directory, or create an
  automatic backup.
- The manifest records run ID, corpus root, DDL root, strategy, locale, Neo4j database, LLM config,
  prompt-tree hash, and start time. Secrets are never recorded.
- The manifest is created before the first graph wipe or LLM call and finalized without changing the
  captured input identity.

### US5 — CLI and CI resolve the same explicit analysis profile

An operator can select a corpus without moving data or editing `.env`.

Acceptance:

- A profile schema binds `data_dir`, `strategy`, `neo4j_database`, `locale`, and `run_output`.
- A profile is an optional explicit input, never an implicit default.
- Explicit CLI arguments override profile values; profile values override environment/default values.
- Preflight prints the resolved `source`, `ddl`, strategy, database, locale, and output location.
- Missing `source`, blank/`system` database, invalid strategy/locale, or unsafe output location fails
  before graph mutation or LLM calls.
- Local CLI and CI use the same resolver and validation function.

### US6 — Generality is tested, not asserted

Acceptance:

- Contract fixtures cover at least Java, Python, SQL, and one C-like source shape.
- Fixtures span unrelated domains and infrastructure code.
- The same expected contract is applied without language/domain conditionals in product code.
- A static test rejects corpus-specific evidence terms in changed product logic and prompts.
- Relevant regressions pass before the official corpus analysis and semantic rejudgment begin.

### US7 — Module descriptions use only semantic analysis targets

A module/class/file description is synthesized from the summaries of the code units that the active
strategy actually analyzes. Structural nodes retained for graph topology do not suppress module
description generation merely because they intentionally have no semantic summary.

Acceptance:

- Module summarization consumes `strategy.analysis_targets`; it does not maintain a second
  language-specific label list.
- Framework control nodes such as branches and loops remain in the graph but are excluded from the
  member-summary completeness check.
- DBMS ranges selected by the DBMS strategy remain eligible without a Framework-specific branch.
- A missing summary on a real semantic target remains an explicit failure; no sibling field is copied
  into it and no partial module description is silently presented as complete.

### US8 — Every model-visible source excerpt has one explicit coordinate contract

Analyzer keeps exact source text for deterministic parsing while exposing a separately rendered,
line-numbered excerpt to models, logs, graph consumers, and source-detail APIs. Adding line prefixes
must never alter the text consumed by lexers, regular expressions, call linking, literal extraction,
or evidence membership checks.

Acceptance:

- One shared `SourceExcerpt` owner creates both exact `raw_text` and rendered `numbered_text` from a
  file path and absolute inclusive source range.
- `numbered_text` prefixes every source line with its absolute physical file line and declares the
  file, range, and `absolute-file-line` coordinate system.
- Source-backed graph-node `code_text` is the numbered representation. Internal deterministic
  consumers use the separately preserved raw representation and never strip prefixes heuristically.
- Framework and DBMS prompts use the same absolute-file-line coordinate system. Branch indexes,
  rule anchors, child placeholders, ancestor excerpts, and external source excerpts refer to those
  same coordinates.
- A compacted child placeholder retains its original absolute range and explicitly states that it is
  a context summary, not executable source.
- Empty/read-failed/compacted input states are explicit metadata; blank text is never ambiguous.
- Prompt context separates `TARGET`, `DIRECT_EVIDENCE`, `SURROUNDING_CONTEXT`,
  `CALLEE_INTERFACE`, and `SOURCE`. Surrounding/module/callee text cannot be mistaken for behavior
  directly implemented by the target.
- Stable instructions remain in the shared prompt prefix and volatile evidence remains in the user
  suffix. The renderer adds no per-line JSON object or other avoidable token overhead.
- Table-role prompts receive the same deterministic read/write sets and authoritative single-operation
  evidence used by storage validation. A closed role that contradicts those facts is rejected as a
  whole item; Analyzer does not rewrite its natural-language role or usage summary.
- Repeating scenario arrays use an evidence-derived schema safety ceiling above the observed normal
  maximum, rather than an oversized corpus-count-based ceiling. Reaching the ceiling remains an
  explicit quality finding, never an invitation to synthesize or merge examples.
- Deterministic table-access matching operates only on language-lexed active source. Comment and
  inactive-block masking preserves physical line count and quoted executable values, so relationship
  `evidence_line` remains the exact absolute source coordinate used by Catalog and frontend
  highlighting.

### US9 — Post-analysis synthesis cannot silently exclude authoritative schema targets

Every Analyzer-owned table in the parsed DDL is a table-description target. Function-level
`table_insights` add direct-use evidence, but their presence is not an eligibility gate for the
table or any DDL-owned column.

Acceptance:

- The table-level target set comes from `state.tables`, in deterministic source order.
- A table with zero function-level insight still receives one table-description request containing
  its original table metadata, complete DDL-owned column list, and any deterministic direct R/W
  evidence.
- Every DDL-owned column is required exactly once in the structured response.
- Missing insight never causes a table/column to disappear and never authorizes copying a sibling
  meaning, a first candidate, or a source description into `analyzed_description`.

### US10 — Rule occurrences carry positive source evidence and exact duplicates fail closed

Every Rule occurrence cites an active absolute source line that directly supports its contract.
Repeated meaning at different source occurrences remains legal, while an exact duplicate at the
same anchor with the same scenarios is invalid model output.

Acceptance:

- `anchor_line` is a required positive absolute physical file line.
- Conditional rules anchor to the decision that selects the result. Unconditional rules anchor to
  the first active line that directly performs the observable transformation, delegation, I/O,
  state change, or return.
- Framework and DBMS structured schemas reject `0`, omission, negative values, and booleans.
- The Framework response schema and DBMS keyed fan-in reject an exact duplicate
  `(statement, anchor_line, examples)` within one routine item. DBMS preserves valid sibling items
  and the concern-level retry mechanism requests only the failed key again; Analyzer never
  deduplicates, merges, rewrites, or substitutes its meaning.
- Equal statements at different anchors remain distinct routine-local occurrences.

## Evidence baseline (not product tuning data)

- Fixed gold: 23 source files, 44,142 LF, 416 routines, 1,382 claims.
- Representative scope: 71 functions + 66 schema nodes.
- Decisive verdicts with empty evidence: 0 after 17 evidence backfills.
- Run-110 prompt body: initial summary/rules bodies equal for 416/416; compact markers 0/416.
- Historical final scalar values equal their logged last stage for 71/71; the former summary-rewrite
  stage is evidence of the defect and has been removed from the current product.
- Unscoped audit traversal leaked 30 Examples across shared Rules; the product evidence reader already
  demonstrates the correct function-prefix ownership pattern.

These facts identify failure mechanisms. They MUST NOT be converted into customer-, corpus-, or
language-specific branches.

## Out of scope

- Moving, deleting, or editing the fixed gold.
- Changing executable source behavior. A later explicit user instruction authorized comment-only
  correction in the analysis source; it is backed up in the scratchpad and guarded by unchanged
  executable-code hashes and line counts.
- Putting adjudication harnesses or ledgers in the Analyzer repository.
- Reverting, staging, or committing pre-existing dirty WIP.

## Success criteria

- SC-001: All acceptance criteria above have executable local evidence or an explicit verification
  artifact.
- SC-002: Changed product logic and prompts contain zero corpus/customer-specific branches or examples.
- SC-003: Prompt material instructions use paired good/bad examples.
- SC-004: Multilanguage/multidomain contract suite and affected regressions pass.
- SC-005: A simulated default run updates the established flat logs contract, while a simulated
  explicit-output run uses the exact supplied directory and creates no implicit child or backup.
- SC-006: Profile precedence and preflight failure timing are proven without editing `.env`.

## Clarifications

### Session 2026-07-27

- Q: May fixes specialize for the observed C/project corpus?  
  A: No. Every product fix must be universal across projects, languages, and domains.
- Q: How are natural-language fields compared?  
  A: By semantic equivalence or sufficient similarity. Character equality is not required.
- Q: Which values require exact equality?  
  A: Graph node/relationship topology and cardinality are the 100% quality contract. Source hashes,
  evidence traceability, and ontology membership remain binary integrity prerequisites rather than
  semantic quality scores. Meaning fields, including stereotype classification accuracy, target
  80–90%.
- Q: May a selected routine have empty `summary` or `search_text` because the model calls it
  meaningless?  
  A: No. Selection is a structural upstream decision. Every selected routine gets grounded text; a
  declaration or empty body describes that fact instead of returning an empty string.
- Q: How should prompts teach the contract?  
  A: Every material instruction must include both a correct and an incorrect example, written
  generically.
- Q: Is an official reanalysis part of this feature?  
  A: Yes. After the universal fixes and all local regressions pass, run the fixed SKT corpus through
  the real CLI with one explicitly isolated output directory and re-adjudicate it against the same
  fixed gold. Do not start the official run before those gates pass.
- Q: May one generated semantic field fill another missing semantic field?  
  A: No. `summary`, `search_text`, rules, examples, questions, names, and meanings must each come from
  their own LLM contract. A missing field is a validation/retry failure, never permission to copy a
  sibling field. The explicit table exception is `description`: it is not the LLM field, and an empty
  source description may be filled from the separately generated `analyzed_description`.
- Q: May Analyzer call another LLM to improve, review, repair, merge, or rewrite a generated field?  
  A: No. Each semantic producer gets one authoritative output path. Transport retries or a focused
  request for a batch item that produced no output do not rewrite an existing meaning. The only
  separate semantic-compression call allowed is context summarization, which is input context and
  never a stored output-field substitute.
- Q: Which automatic mutations are forbidden?  
  A: Semantic rewriting, replacement from another meaning field, copying the first candidate,
  automatic empty-value filling, and automatic shortening/truncation of stored semantic content.
  Exact ontology validation, evidence/ownership validation, and count/cardinality checks remain.
- Q: Who makes the final semantic-equivalence judgment?  
  A: The working agent directly reads source, fixed gold, and actual output for every selected field.
  Harness code may extract records, enforce evidence completeness, and total already-decided
  verdicts, but another LLM judge is not authoritative and may not generate the final yes/no ledger.
- Q: Do control-flow nodes need summaries before a file/class module can be summarized?  
  A: No. They remain structural graph evidence. Module description completeness is evaluated only
  across `strategy.analysis_targets`, so analyzed functions/methods drive the module description.
- Q: Should line numbers be inserted directly into the only stored source string?  
  A: Model-visible and graph-visible `code_text` is line-numbered, but Analyzer preserves exact raw
  text separately for deterministic consumers. Prefix stripping is not an accepted recovery method.
- Q: Which coordinate system is authoritative in prompts and evidence?  
  A: Absolute physical file lines. Routine-relative offsets may exist only as internal derived
  coordinates and must never be presented as the authoritative model-visible line.
- Q: May a branch condition be replaced with a generic complement because its token count is small?  
  A: No. Branch role comes from the parser's structural node kind and language ontology. Only an
  actual ELSE/default branch is a complement; IF/CASE conditions remain verbatim, and a
  SWITCH/match controller is context rather than a selectable outcome branch.
- Q: How are repeated identical Rule statements inside one routine represented?  
  A: The shared Rule meaning node may remain deduplicated, but every routine-local occurrence has a
  deterministic ordinal and anchor on HAS_RULE. Example identity includes that occurrence, so graph
  cardinality exactly preserves repeated occurrences.
- Q: How many R/W source coordinates are retained?  
  A: Every distinct `(file, line, dml_type)` occurrence for a `(source, table, relation)` edge,
  sorted deterministically. Catalog expands them into independently clickable reference rows and
  frontend highlighting uses each row's absolute line.
- Q: May symbol/type/SQL-key names be grouped as glossary aliases by substring resemblance?  
  A: No. Alias candidacy requires compatible provenance/kind and explicit same-entity or normalized
  spelling evidence. Related entities remain related, not aliases; uncertainty keeps terms separate.
- Q: Which columns enter the per-routine table-insight prompt?  
  A: Only columns directly evidenced by that routine. The later table-level synthesis receives the
  complete DDL-owned column list exactly once and must return every column exactly once.

### Session 2026-07-28

- Q: What did the one official rerun reveal after the local contracts passed?  
  A: The isolated run completed 1,146 LLM calls and preserved the 23/23 source hashes. Representative
  function/schema IDs and representative CALL fan-in/out were exact. Natural-language quality rose
  in several fields, but the run exposed four independent residual mechanisms: one parsed table and
  its five columns were excluded because table-level synthesis was gated by function insights;
  95 Rule occurrences used the explicitly permitted zero anchor; two routine items contained exact
  same-anchor Rule/Example duplicates; and stereotype errors clustered at underspecified boundaries
  such as lifecycle versus request reset, primitive helper versus cross-cutting utility, query versus
  boundary adapter, and router versus unified validation/orchestration.
- Q: Does a nonzero Rule anchor have to be one of the branch-index lines?  
  A: No. It must be an active absolute source evidence line. A conditional rule uses its selecting
  decision line; an unconditional contract uses the active line that performs the observable effect.
  Therefore “not present in branch_index” alone is not a defect.
- Q: May a table-level LLM call be skipped because no routine emitted a table insight?  
  A: No. Parsed DDL ownership defines table and column eligibility. Insights and deterministic R/W
  edges are evidence only.
- Q: Is adding one short instruction and hoping for a better rerun an acceptable completion gate?  
  A: No. Before the user-authorized second official rerun, every residual failure family must be
  traced through actual input, rendered prompt, response schema, retry/fan-in, persistence, and
  verifier behavior. The fix must concentrate a complete decision procedure and paired
  counterexamples in the owning section, lock deterministic ownership/evidence invariants in code,
  refresh intentional prompt hashes, and pass adversarial multilingual/multidomain contracts plus
  the full suite.
- Q: Is a second official rerun now authorized despite the earlier no-second-rerun boundary?  
  A: Yes. The user explicitly superseded that boundary on 2026-07-28. Exactly one new isolated CLI
  run is allowed after all preflight gates pass, followed by the same fixed-gold structural and
  semantic delta verification.
- Q: What did the direct rerun-2 residual audit find after the second run?  
  A: The remaining active misses were not parser, JSON, merge, or write losses. They clustered in the
  first authoritative Rule response: independent branches were compressed into phase-summary Rules,
  outbound keys/messages/records were mistaken for disposable buffer work, and positive alternatives
  or failure-with-continue paths were omitted. Three reported misses were verifier false negatives
  because separate generated Rules together entailed the gold claim; one omitted path also exposed an
  over-broad generated Rule and must be counted as a faithfulness contradiction rather than silently
  left as missing.
- Q: May deterministic product logic create the missing meanings?  
  A: No. The owning shared prompt contract must make the complete inventory decision once, with neutral
  good/bad examples. Framework and DBMS may keep only their strategy-specific node/anchor/ownership
  instructions; duplicated semantic policy is removed. The most important Framework branch checklist
  is placed after lower-priority volatile context. No post-hoc semantic author, rewrite, merge, or
  field fallback is introduced.
