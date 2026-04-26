---
name: entity-analysis
description: >
  **When**: category = entity, bean, cmp_mapping, artifact, entity_fqn, candidates, profile_only.
  **Task**: starting from startpoints / profile map, use `bundle_search` to find real DB-access
  functions, then register READS/WRITES · FK · COLUMN edges.
  **Do NOT**: use for `ddl` / `ddl_tables` (use `table-tracking`), unmatched SQL keys (`sql-tracking`),
  or config keys (`config-tracking`).
---

# Entity Analysis Skill

## Common Principles (all categories)

- **No early exit**. Business modules typically have 10~20+ table accesses. Do not stop on "looks like nothing more".
- **0 hits → retry ≥3 times** with variants (case, underscore, partial, plural).
- When you find a table, re-search by its name (`bundle_search(table, kind="text")`) to catch **all** access functions.
- For each READS/WRITES edge, inspect the function's struct/DTO fields — if they map to columns, call `graph_create_column_tool`.

## Core Loop (strict — applies to every category)

**Search → Read → Register → Follow-up search → repeat**.

1. `bundle_search(pattern, kind)` → candidate list (chunk_id + file + span).
2. For **each** candidate:
   - `bundle_get_chunk(chunk_id)` → read actual body.
   - Verify it's a real access (not a comment, declaration, or false match).
   - Register **immediately** via `graph_create_table_access_tool` with evidence from the body.
   - Extract follow-up keywords: table literal, constant, variable name, header include — anything that points to more usages.
3. Run next `bundle_search` with those keywords → loop until no new candidates.

**Register as you find — never batch. No evidence → no edge.**

---

## Category handling

### ▶ category = profile_only (no startpoints; profile map only)

Even if `items=[null]`, **there is work to do**. The target is the `[Project Framework · DB Access Pattern Analysis — Search Map]` block. That block is the **map of how to find all DB accesses**.

#### Strict procedure

Follow exactly. Do NOT skip steps. Do NOT re-narrow already-searched keywords.

**Step 1 — read the map** → identify:

- Access mechanism (function/macro/method names)
- Identifier rule (where/how table id is embedded + how to strip it out)
- Naming rule (prefix/suffix → READS/WRITES)
- Column Source (where column fields appear, e.g., struct/DTO/annotation)
- Initial search keywords (primary access function name is almost always required; plus any prefix pattern)

**Step 2 — one broad search per keyword**:

- Call `bundle_search(query=<keyword>, kind="text")` (prefer `text` over `symbol` to match body content)
- Store the FULL returned chunk_id list mentally as "the work queue"

**Step 3 — iterate the queue one by one — register AS YOU FIND**:

For **each chunk_id** in the queue (first to last, do not cherry-pick):

1. `bundle_get_chunk(chunk_id)` — read the body.
2. Apply the map:
   - If the body shows a real DB access (per the Identifier Rule) → **immediately** call `graph_create_table_access_tool` with: the **stripped** table name (apply Identifier Rule — remove the `_<crud>####` suffix or equivalent), READS/WRITES per Naming Rule, evidence = quoted code line, source_function_id = this chunk_id.
   - In the same turn, inspect struct/DTO/entity fields visible in the body. For each field that maps to a column of this table → `graph_create_column_tool(table, column, dtype, description)`. Do this BEFORE moving to next chunk_id.
   - If the chunk is a false match (comment, declaration only, unrelated mention) → skip with a one-line reason.
3. While reading, collect **follow-up keywords** for the next round (other table literals, constants, variables pointing to identifiers).
4. Move to the next chunk_id. Do NOT issue a new `bundle_search` until every chunk_id in the current queue is processed.

**Step 4 — reverse-trace each registered table**:

For each distinct table you registered in Step 3 → `bundle_search(query=<table_name>, kind="text")`. Apply Step 3 to the returned queue. This catches access points that used the table indirectly.

**Step 5 — exhaust follow-up keywords**:

Consume the follow-up keyword list (Step 3 collected these). Each becomes a fresh Step 2 + Step 3 pass.

**Step 6 — self-check before exit**:

- Did you iterate the FULL chunk_id list from each `bundle_search` (not just 2~3)?
- For each registered table, did you reverse-search?
- Did you register columns together with every table edge?
- Are all follow-up keywords consumed?

Only when all YES, exit. A typical business module yields 10~20+ edges. If fewer than 5, you almost certainly skipped candidates — go back to Step 3.

#### Anti-patterns to avoid

- **Do NOT** pick 2~3 "representative" candidates and stop.
- **Do NOT** re-narrow with a specific function name right after a broad search — just `bundle_get_chunk` the specific chunk_id directly.
- **Do NOT** defer column registration to a later pass — fields are only visible in the body you just read.
- **Do NOT** exit because "pattern looks confirmed" — the goal is exhaustive coverage, not pattern proof.

---

### ▶ category = entity · bean · cmp_mapping · artifact

Each item: `{key, target_type, chunk_type, file_path, text_snippet}`.

1. Extract class / entity / table identifiers from `text_snippet`.
2. `bundle_search(id, kind="text")` + `bundle_search(id, kind="symbol")` (0 hits → retry ≥3 with partial/short forms).
3. For each candidate, read body → determine CRUD → `graph_create_table_access_tool`.
4. If FK is visible in original text or code, `graph_create_fk_tool`.
5. DTO fields of confirmed function → `graph_create_column_tool`.

### ▶ category = entity_fqn (Java FQN strings like `com.acme.User`)

1. `bundle_search` the last segment (class name) first.
2. 0 hits → try full FQN, then partial path, then short name.
3. Track entity users (Repository/DAO/Service) → real CRUD functions → edges + columns.

### ▶ category = candidates

Each item: `{fqn, file_path, annotations, comment}`.

**Entity decision first**:

- ✅ Used as SQL/ORM argument, has 1:1 DB column fields (id, created_at...), data-class annotation, DB-mapping methods
- ❌ Enum / exception / config / Service / Manager / Controller / Repository / Handler / abstract / interface → skip

If entity confirmed, `bundle_search` by class/FQN → real access functions → edges + columns.

---

## Chain-trace patterns (all categories)

Depending on how the startpoint appears in code:

1. **Direct** — startpoint appears in the function body → edge on that function.
2. **Via variable** — stored in a variable/constant → search the variable → edge on referencing function.
3. **Via helper** — helper receives the startpoint → edge on helper's **caller** (not the helper itself).
4. **Via mapper/repo** — goes through a mapper definition or Repository method → edge on the **caller**.

### STOP condition (per trail)

One table may have multiple usage trails. For each trail:

- (a) reach the CRUD-executing function body, OR (b) reach its call site

→ register edge → terminate **this trail only**. No infinite recursion up. **If other trails exist, start fresh.**

## Registration rules

### READS / WRITES

- `table_name`: real table name (keep casing as declared)
- `source_function_id`: **must** be a real `chunk_id` from `bundle_search`
- `evidence`: direct quote of the code line
- `access`: READS (SELECT / read) / WRITES (INSERT/UPDATE/DELETE)
- **Self-check before register**: "which code line is the evidence?" If you cannot answer immediately → do NOT register.

### Columns (do not omit)

- Inspect struct/DTO fields used in the function.
- If a field maps to a table column, call `graph_create_column_tool(table, column, dtype, description)`.
- No evidence → do not register. If evidence exists → **must** register.

### FK

- `from_table` = FK column side (child), `to_table` = PK side (parent).
- No self-references, no duplicates, no speculation.

## Completion checklist

- [ ] Every candidate from profile/startpoints checked
- [ ] For each table, every access function searched (not just 1~2)
- [ ] 0-hit keywords retried ≥3 times
- [ ] Every READS/WRITES function reviewed for columns
- [ ] No edges without solid evidence

All yes → done. Any no → keep going.
