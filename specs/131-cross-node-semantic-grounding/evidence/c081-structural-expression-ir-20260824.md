# C-081 structural expression IR evidence — 2026-08-24

## Boundary and sealed actual input

- Boundary: Parser emits only grammar-owned roles, exact ranges, lexical scope, and recursive
  ANTLR rule/token structure. It emits no binding, type, linkage, resolution, or meaning.
- Analyzer validates the wire contract, binds it to the compatibility AST by ordered exact source
  equality, then maps C grammar vocabulary into a language-neutral internal tree.
- No source regex/reparse, semantic fallback, arbitrary cap, unresolved graph node/label, new DB,
  or Architect access was used.
- SKT source: `apascrtu0010t02.c`, SHA-256
  `edd512c6478cb31f7a0f396f92ec850659f2720d25c553b8a4f4e42ba330d126`.

## Cause closure

1. Parser formal red: the initial contract saw zero `structural_expression` facts.
2. First Analyzer actual red: legacy expression fields 623 vs canonical facts 533.
3. First mismatch: `apascrtu0010t02.c:232`, local declaration initializer
   `long rc = RC_NRM;`.
4. Producer cause: `CAstListener.emitDeclarator` created a compatibility `ASSIGNMENT` but emitted
   no structural fact. The SKT file had 45 such initializers; two legacy target/value fields each
   explained the 90-count gap exactly.
5. Fix: Parser marks `statementOrigin=declaration_initializer` and emits one exact
   `initializer_value` grammar tree. Analyzer uses the Parser-owned target name and does not invent
   a source range for a synthetic assignment target/value.

## Verified ledger

| Coordinate | Result |
|---|---:|
| Parser structural expression facts | 578 |
| Parser completeness population/emitted/unresolved | 578 / 578 / 0 |
| Analyzer structural facts | 578 |
| Analyzer neutral semantic projections | 578 |
| Duplicate or missing fact IDs | 0 |
| Parser artifact SHA-256 | `94c5bbe98bfbf51b6dabc564c73361c417fb53d32b4308689a8988ab68b6a831` |
| Analyzer neutral ledger SHA-256 | `0071beb86434181140348d6a34b28554131b2bc504c466b437722f4ca3f1df46` |
| First projected coordinate | `apascrtu0010t02.c:232:initializer_value` |

## Focused regression

- Parser: `CStructuralExpressionIr2ContractTest`,
  `CStructuralEvidenceIr2ContractTest`, `StructuralStatementAstContractTest`: pass.
- Analyzer step2: 130 passed, 2 skipped.
- Analyzer sealed SKT replay: 1 passed; two in-memory neutral ledgers byte-identical.
- Existing IR 2.0 artifacts without the additive `structural_expression` completeness row remain
  valid. New artifacts that declare the row are validated and projected exact-once.

## Verification boundary

This closes structural transport and Analyzer normalization only. It does not promote notation
syntax to semantic accuracy and does not yet replace the model-authored atomic meaning slot.
The next slice must consume this neutral IR in the Analyzer-owned discriminated semantic schema
and render Python/Rule/Example deterministically.
