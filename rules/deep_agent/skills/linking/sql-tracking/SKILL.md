---
name: sql-tracking
description: >
  **When**: category = `sql`. Data contains unmatched SQL keys (sql_key, sql_type, file_path).
  **Task**: trace each `sql_key` in code to find **every** function that executes it and register
  `(FUNCTION)-[:CALLS_SQL]->(SqlStatement)`. If table access is visible, also register READS/WRITES.
  **Do NOT use for**: entity/class tracking (`entity-analysis`), DDL reverse-lookup (`table-tracking`),
  config keys (`config-tracking`).
---

# SQL Tracking Skill

## Core Loop

**Search → Read → Register → Follow-up → repeat.**

For each sql_key:

1. `sql_lookup_tool(key)` → get full SQL (sql_type, sql_text, file_path).
2. `bundle_search` from multiple angles:
   - full sql_key: `bundle_search(sql_key, kind="text")`
   - id portion: `bundle_search("selectUser", kind="text")`
   - namespace: `bundle_search("UserMapper", kind="symbol")`
   - file name: `bundle_search("UserMapper.xml", kind="text")`
   - partial string: `bundle_search("selectUser", kind="symbol")`
3. For each candidate: `bundle_get_chunk` → read body → verify real usage.
4. Register **immediately**: `graph_create_refer_to_tool("SqlStatement", sql_key, evidence, source_function_id)` → CALLS_SQL edge.
5. **Optional** but encouraged if evidence is clear:
   - Table name visible in SQL body → `graph_create_table_access_tool`
   - FK visible → `graph_create_fk_tool`

## Rules

- `source_function_id` **must** be a real chunk_id from `bundle_search`.
- One SQL may be called by multiple functions — find them **all**.
- 0 hits → retry ≥2~3 times with variants.
- No evidence → no edge. Do not invent.
- Register as you find — never batch.
