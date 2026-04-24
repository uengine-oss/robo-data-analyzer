---
name: table-tracking
description: >
  **When**: category = `ddl` or `ddl_tables`. Data contains DDL table metadata
  (name, columns, PK, FK).
  **Task**: for **each** DDL table, `bundle_search` to find **every** function that uses it
  and register READS/WRITES edges. Register new FK edges discovered in code.
  **Do NOT use for**: entity/class without DDL (`entity-analysis`), SQL keys (`sql-tracking`),
  config keys (`config-tracking`).
  **Note**: columns are already secured from DDL — do NOT call `graph_create_column_tool`.
---

# Table Tracking Skill

Reverse-lookup from DDL table metadata to code usages.

## Input Data

- Table list: `[(name, meta), ...]` or `[{name, columns, primary_keys, foreign_keys}, ...]`
- `[existing FK list]` if present → **do not duplicate**
- `[XML / annotation entity hints]` → reference for DDL↔entity mapping

## Goals

1. Register READS/WRITES on **every** function that uses each table.
2. Register **new** FK edges found in code (no duplicates of the existing list).
3. **Do NOT** register columns (already in DDL).

## Core Loop (per table)

**Search → Read → Register → Follow-up → repeat.**

1. `bundle_search(table_name, kind="text")` → candidates.
2. For each candidate: `bundle_get_chunk(chunk_id)` → verify real access.
3. Register **immediately**: `graph_create_table_access_tool(table_name=DDL-cased name, access, evidence, source_function_id)`.
4. Do not stop at one — search exhaustively.
5. 0 hits → retry ≥2~3 times (partial, case, plural).

### READS / WRITES Rule

- **READS**: SELECT, query functions (`_s` suffix, find/get/select/query, etc.)
- **WRITES**: INSERT/UPDATE/DELETE, mutation functions (`_i`/`_u`/`_d` suffix, save/insert/update/delete)
- Must verify in body — no guessing.
- `evidence`: quote the concrete SQL / call line.
- A single function may both READ and WRITE the same table.

## FK Tracing

After table edges:

1. `bundle_search("CREATE TABLE", kind="text")` → inspect `REFERENCES` clauses.
2. `JOIN ... ON` clauses from prior searches → extract FK.
3. `table_columns_tool` → `xxx_id` naming suggests FK candidate.

### FK Registration Rule

- Verify both sides exist via `table_search_tool` / `table_columns_tool`.
- `from_table` = FK column side (child), `to_table` = PK side (parent).
- No self-references, no duplicates.
- **If the existing FK list contains it, do NOT duplicate.**
- Skip if unsure.
