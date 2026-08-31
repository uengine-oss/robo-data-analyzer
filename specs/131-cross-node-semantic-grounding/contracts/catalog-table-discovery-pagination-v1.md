# Catalog table discovery pagination v1

## Ownership and endpoint

- Catalog owns snapshot creation, cursor validation, deterministic ordering, and page
  production for `GET /robo/tables/discovery`.
- Analyzer owns strict page validation and aggregation. It may pass the inventory to the
  DDL union only after the terminal page proves exact closure.
- Snapshot and cursor values are transport controls only. They are never graph products,
  semantic evidence, or LLM input/output.

## Request

The first request contains:

- `datasource`: non-empty datasource name
- `sample_limit`: non-negative integer, currently `5`
- `page_size`: positive integer, currently `1000`; this is a transport size, not a
  datasource population cap

The first request contains neither `snapshot` nor `cursor`. Every continuation request
contains both opaque values returned for the same snapshot. Analyzer does not rewrite,
decode, or infer either value.

## Response

Every successful page is one JSON object with exactly this versioned envelope:

- `schema_version`: `catalog-table-discovery-page/v1`
- `datasource`: the requested datasource
- `snapshot`: non-empty opaque immutable-snapshot token, identical on every page
- `page_index`: zero-based contiguous non-negative integer
- `total_tables`: non-negative integer fixed when the snapshot is created and unchanged
  across pages
- `tables`: ordered array of table objects
- `next_cursor`: non-empty opaque string for a continuation, or `null` only on the
  terminal page

Each table has non-empty `name`, optional string `catalog` and `schema`, boolean
`catalog_quoted`, `schema_quoted`, and `name_quoted` flags (default `false`), list
`columns`, list `sample_rows`, and boolean `degraded` (default `false`). Each column has a
non-empty `name`, boolean `name_quoted` (default `false`), and its Catalog metadata such as
`dtype` and `nullable`. Every sample row is an object.

`truncated`, `max_tables`, `catalog_name`, offset paging, and a legacy one-shot envelope
are not part of this contract and receive no fallback.

## Snapshot and ordering invariants

- Catalog seals one immutable table population before returning page zero.
- It orders that population deterministically by quote-aware
  `(catalog, schema, table)` identity and slices it without omission or overlap.
- Unquoted identifiers compare case-insensitively. Quoted identifiers preserve exact
  spelling. The same rule applies to column identities within a table.
- The sum of all page table counts equals `total_tables` exactly once.
- A zero-table datasource closes on page zero with `tables=[]` and `next_cursor=null`.
- A nonterminal page is non-empty and has a continuation cursor. A terminal page has no
  cursor. Catalog must reject a cursor used with another snapshot or datasource.

## Analyzer failure semantics

- `404` is nullable only on the first request and means the datasource is unregistered.
- Any later `404`, non-200 response, malformed JSON, version or datasource mismatch,
  snapshot/total drift, non-contiguous page, cursor cycle, empty nonterminal page,
  duplicate table or column identity, early closure, count overrun, or terminal cursor
  aborts analysis before DDL union or publication.
- Analyzer has no page-count cap and does not publish a verified prefix. It aggregates in
  provider order, verifies `len(tables) == total_tables`, then invokes the existing DDL
  union and sample-data projection once.
- A table-level `degraded=true` may retain the table identity with unavailable detail, but
  it cannot hide, skip, or remove the table from the snapshot population.
