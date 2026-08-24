# C-085 call-occurrence semantic closure

**Date**: 2026-08-24

**Scope**: Analyzer only; sealed SKT one-file accuracy slice; no Neo4j write

## Sealed inputs

| Input | SHA-256 |
|---|---|
| `apascrtu0010t02.c` | `edd512c6478cb31f7a0f396f92ec850659f2720d25c553b8a4f4e42ba330d126` |
| Parser artifact | `94c5bbe98bfbf51b6dabc564c73361c417fb53d32b4308689a8988ab68b6a831` |

## Confirmed cause and boundary

- Parser already produced one canonical ID and exact call/callee/receiver/argument
  range, source fragment, lexical scope path, grammar rule and presence for every
  call occurrence.
- Analyzer already produced one final resolution ledger row per Parser ID, but
  Step 6 reduced the input to `(line, callee)` and discarded occurrence identity,
  arguments, receiver, ranges and final resolution.
- Branch inventory then removed condition calls by reparsing condition text with a
  name regex. A same-line condition/body call with the same callee therefore lost
  the body call.
- The semantic frame also keyed calls by `(line, callee)`, so repeated occurrences
  could collapse and condition calls had no exact expression dependency.
- The fix joins Parser structure and Analyzer resolution by exact ordered call ID,
  validates every duplicated structural field, assigns `condition/effect/non_active`
  by exact range containment in resolved expression facts, and propagates the
  occurrence through Rule and semantic-frame consumers. It does not infer binding,
  type or linkage from source text.
- Partial upload remains normal. Analyzer resolution records internal, external or
  unresolved/dynamic outcomes on the occurrence; no unresolved graph node/label is
  introduced.

## Actual exact-once result

| Ledger | Result |
|---|---:|
| Parser call facts | 1,157 |
| Analyzer resolution rows | 1,157 |
| AnalysisContext occurrences | 1,157 unique |
| Semantic-frame occurrences | 1,157 unique |
| condition / effect | 183 / 974 |
| Rule effect occurrences | 974 / 974 unique |
| processing-effect slots | 974 / 974 unique |
| condition-to-guard slots | 183 / 183 unique |
| deterministic in-memory replay | byte/value identical |
| first missing/duplicate coordinate | none |
| focused regression | 149 passed + 20 subtests |
| staged-only full regression | 1,299 passed / 8 skipped / 367 subtests |

The sealed actual resolution distribution was internal 84, external 1,054 and
dynamic 19. Those are resolution states, not separate graph node kinds.

Direct structural judgment:

- At `apascrtu0010t02.c:1860`, two `strncmp` calls have distinct IDs and ranges.
  Their second arguments are respectively `"03"` and `"04"`; both remain separate
  condition occurrences linked to the owning condition fact.
- Rule row `R232` owns four `pfm_try` effect occurrences at lines 238, 241, 244 and
  247. Their IDs and arguments remain distinct even though the current final display
  still renders four target-only `call("pfm_try")` leaves.
- The formal same-line `if (check(x)) { check(y); }` counterexample retains the body
  call as one effect instead of subtracting it by callee name.

## Input, instruction and output assessment

| Layer | Current result | Remaining boundary |
|---|---|---|
| Input | Exact occurrence IDs, source fragments, component ranges, scope path, presence and final resolution are retained internally. Prompt projection includes only slot-dependent call meaning fields. | Final call-effect rendering still emits target-only `call("name")`; ordered argument meaning is a later IR slice. |
| Instruction | Existing semantic-frame slot IDs, evidence refs, exact required-key response schema and fail-closed validation remain the mold. No free-form extra field is accepted. | An LLM can still be insufficient on business meaning; that must remain an explicit insufficiency, not fallback success. |
| Output | Repeated same-callee calls have distinct evidence/effect IDs, and Rule/Example exact-once ownership is preserved. | Python notation remains a structured comparison representation, not product acceptance. |
| Efficiency | Sparse occurrence attributes omit null/blank duplicates. The sealed 44-routine projected summary payload is 2,382,537 bytes; largest routine payload is 273,144 bytes. | This is the accuracy-preserving baseline for a later performance slice. It must be reduced by dependency projection/deduplication, not caps, skips or missing evidence. |

No full 23-file SKT run was performed. The final full accuracy/performance run remains
deferred until all related IR effect representation and prompt-efficiency changes are
complete.
