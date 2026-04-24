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

Even if `items=[null]`, **there is work to do**. The target is the `[Project Framework · DB Access Pattern Analysis — Search Map]` block in the user message. That block is not a table list — it is the **map of how to find them**.

⚠ **Never exit with `tool_calls=0` or after only `list_skills`/`read_skill`.** At least 3 `bundle_search` calls from different angles before "no result" is acceptable.

**Step 1 — read the map**: from the profile's `## DB Access Pattern` 4 subsections and `### Symbol Role Classification` table, identify:

1. **Access mechanism** — which function/macro/method performs DB access
2. **Identifier rule** — where / in what form a table identifier is embedded (literal / constant / XML / annotation)
3. **Naming rule** — prefix/suffix pattern distinguishing CRUD
4. **DB-access symbols** — any symbol marked "DB access" in the role table

**Step 2 — choose ≥3 search angles** (combine any of these):

- **(A) Reverse from access mechanism** — `bundle_search` the DB-access function name → extract table identifier from each call site
- **(B) Reverse from naming rule** — `bundle_search` prefix/suffix pattern → read body → extract table
- **(C) Search identifier directly** — if identifiers are string literals / SQL / annotation, search those strings
- **(D) Each "DB access" role-symbol** — search each one

(A) is almost always required when the mechanism is known.

**Step 3 — loop**: for each search result, run the **core loop** above (read chunk → register → follow-up keywords → next search).

**Step 4 — self-check**: ≥3 search angles used? Every table's access functions found? 0-hit keywords retried ≥3 times? Column registration done? If any "no", continue.

> The steps are an example skeleton — go deeper with more angles / more loops as the project requires.

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
