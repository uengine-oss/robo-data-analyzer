# Workflow and Stream Contract

## Final strategies

Framework order:

```text
parse_ddl
(load_ast, load_external)  # parallel only after non-collision test
scan_codebase
scan_external
link_deterministic
link_calls_by_name
link_residual_patterns
link_external
build_context
fetch_table_samples
analyze_code
post-processing group and tail
```

DBMS order:

```text
parse_ddl
(load_ast, load_external)
scan_external
link_dbms_tables
link_calls_by_ast
link_external
build_context
fetch_table_samples
analyze_code
post-processing tail
```

DBMS does not gain Framework scan/residual linkers. External loading is strategy-independent; External Agent runs
only when the inventory contains an unknown eligible text format.

## Step configurable

Steps consume only the existing validated configurable keys:

`thread_id`, `strategy`, `codebase`, `run`, `cost_budget`, `stream`, `neo_client`, `pause`,
`llm_semaphore`, `source_cache`.

No step constructs a second Neo4j client, LLM client, source cache or environment loader.

## Step3 load contract

- scope: `step3.load_external`.
- output: project_files, external_chunks, document_nodes, parse unresolved refs.
- progress: `step_processing_scope` plus `track_processing` per file.
- persistence and stream use Analyzer graph writers and `emit_nodes/emit_edges`.
- file-level failure is isolated and recorded unless the inventory/path/security contract itself is invalid.

## Step5 link contract

- scope: `step5.link_external`.
- requires code_nodes and external chunks; absence caused by prior step failure is explicit error.
- output: external_edges and link unresolved refs.
- no LLM; exact matching and safe constant propagation are deterministic.
- key-index optimization must be result-equivalent to exhaustive matching.

## Step4 unknown-text contract

- scope: `step4.scan_external`; it runs after `(load_ast, load_external)` in both strategies.
- framework and external skill roots are disjoint; framework scanning never reads runtime external skills.
- one stored matching skill is revalidated and applied with zero LLM calls.
- absent or invalid matching skills use the shared budget, semaphore and pause controller; only n=7/agree=4 consensus
  with at most two repairs per candidate can be persisted atomically.
- rejection remains an explicit `UnresolvedRef`; raw or unvalidated regex never reaches chunk creation.

## Step6 context contract

- include only chunks linked to the current analysis node or exact document mentions relevant to it.
- reconstruct parts in ascending part_index and verify content hash.
- include file path, key/kind and bounded content; never inject entire project inventory.
- enforce existing context/token budget and deterministic truncation.

## NDJSON

Node event requires `id`, single `label`, `labels`, and display props. Relationship event requires exact `rel_type`,
`from.id`, `to.id`, persisted semantic props. Unknown node label or edge class fails closed.

Frontend step keys:

- `step3.load_external`
- `step4.scan_external`
- `step5.link_external`

`emit_message(stream, content)` remains two arguments. Step identity comes from processing scope events.

## Cancellation and termination

File parse tasks and agent fanout tasks are registered, cancelled and awaited. Pause/stop/cost cancellation is never
converted into per-file failure. Normal completion emits exactly one stream_end through the existing run lifecycle.
