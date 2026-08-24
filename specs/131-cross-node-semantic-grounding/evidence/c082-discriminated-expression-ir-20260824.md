# C-082 Discriminated Expression IR Evidence — 2026-08-24

## Scope and boundary

This slice consumes the C-081 Analyzer-neutral grammar tree and produces strict,
language-neutral expression variants. It does not parse source text, decide binding,
type, linkage or runtime meaning, write graph nodes, call an LLM, or re-run Strategy A.

Producer → consumer before this slice:

`Parser structural_expression → C grammar adapter → semantic_expression_facts → no consumer`

The first formal red was the missing discriminated core. The actual input also showed
that the grammar-neutral forms retained verbose wrapper chains and did not directly
distinguish calls, members or binary operators.

## Sealed actual input

- Source SHA-256:
  `edd512c6478cb31f7a0f396f92ec850659f2720d25c553b8a4f4e42ba330d126`
- Parser artifact SHA-256:
  `94c5bbe98bfbf51b6dabc564c73361c417fb53d32b4308689a8988ab68b6a831`
- First coordinate: `apascrtu0010t02.c:232:initializer_value`

## Actual causal and completeness results

- Every one of 578 expression facts names an existing CodeNode.
- Following only `parent_id` assigns 578/578 facts to 46 routines; orphan count is 0.
- Ledger population is structural 578 → grammar-neutral 578 → discriminated 578.
- Top-level variants: reference 212, binary 173, member 127, call 44, group 10,
  unary 6, literal 4, cast 2.
- Missing, duplicate and unresolved classification counts are all 0.
- Two reads are byte-identical. Discriminated ledger SHA-256:
  `589e09e7cd60b454ecd48f1ac2cf37e393428a6cfead577e8c1f2f4f989ef58f`.

## Contracts and tests

- Variant schema is closed and retains exact source ranges.
- Operators are normalized to language-neutral names; member access retains direct vs
  pointer access; calls retain ordered arguments; unsupported shapes fail closed.
- Focused Step 2 regression: 131 passed, 2 skipped.
- Sealed SKT replay: 1 passed.

## Non-promotion and next consumer

This proves internal structural classification only. It does not prove semantic accuracy,
Python notation correctness or product acceptance. The next consumer must assign facts to
their routine by structural ownership, expose only slot-referenced expression closure, and
render Python/Rule/Example deterministically. Dumping all verbose grammar trees into every
prompt is explicitly not the intended path.
