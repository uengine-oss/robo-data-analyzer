# DDL logical-name coverage

## Goal

Preserve LLM-generated logical names by attaching every accepted table insight
to the exact deterministic DDL table/column identity.

Only tables in the code node's deterministic READS/WRITES set and columns in
that table's injected DDL allowlist may survive. Missing coverage and rejected
identities must remain observable rather than being hidden by invented names.

## Contracts

- Table identifiers survive only when they exactly equal an injected
  deterministic R/W table id. Separator, case, prefix, duplicated-terminal, and
  bare-name variants are rejected rather than normalized or inferred.
- Accepted LLM column logical names flow through aggregation, Neo4j updates,
  and artifact state without being replaced by DDL comments.
- Every deterministic READS/WRITES table is presented independently; multiple
  related tables remain multiple `table_insights` entries.
- Framework and DBMS serialize R/W context as the same JSON record shape:
  `table_id`, `description`, `columns`, and `sample_rows`. The exact id is never
  concatenated with description text or column labels.
- Project skills may define verified raw-source patterns for compile-time adjacent
  string literals. The generic linker never rewrites or joins source text.
- When a project skill matches, its `ScanResult contract` is copied without regex
  rewriting, pattern merging, DDL-name enumeration, or identifier normalization.
- Regex verification rejects a captured entity when identifier characters such as
  `_`, quoted `-`, `.`, or `$` remain immediately outside the capture. A shorter DDL
  name that happens to match the captured prefix does not make the pattern valid.
- Each accepted column exactly equals a column from that table's injected DDL
  allowlist. Case variants and code-only names never create a COLUMN or logical name.
- Rejected tables/columns and an expected table omitted by the LLM are logged
  with code id, table id, and rejected/missing names.
- Physical/schema mismatches are not silently renamed or invented.
- Empty logical names remain empty or eligible for later LLM processing. DDL
  comments/descriptions are never copied into logical names.

## Failure scenarios

- Ambiguous equal table names across schemas are not matched by bare name.
- If no DDL column allowlist exists, the accepted column list is empty.
- A logical name with an empty description is still preserved through step 7.
- Existing non-empty logical names and analyzed descriptions are preserved.

## Observed root causes (2026-07-14 latest artifact)

- `update_order_shipping` received both `db:orders` and `db:shipping`; the LLM
  returned both as duplicated-terminal variants and the old filter dropped both.
- A fresh framework run showed `db:comm_code: <DDL description> | 컬럼: ...` was
  presented as one scalar line, so the LLM echoed the entire line as the table id.
  Exact validation correctly rejected it, exposing the input-contract ambiguity.
- The same function's `UPDATE orders` was split across adjacent C string
  literals (`"UPDATE orders "` + `"SET ..."`). The Shopmall skill pattern did not
  model the raw quote boundary, so the linker passed `writes: none` to the LLM.
- A later Scan Agent rewrote the four Skill patterns into one DDL-name alternation
  with `product` before `product_stock` and `product_price_hst`. Python regex chose
  the first successful prefix, so both longer names were linked incorrectly as
  `product`; match-count/DDL-resolution-only verification failed to detect it.
- `shipping_request` returned code-only names such as `shipping_seq`,
  `dlv_co_cd`, `ship_st_cd`, and `reg_dtm` that are absent from the shipping DDL.
  The old boundary validated table ids only, not columns.
- Step 7 iterated only columns with non-empty descriptions, so a valid
  logical-name-only result was lost during aggregation.
