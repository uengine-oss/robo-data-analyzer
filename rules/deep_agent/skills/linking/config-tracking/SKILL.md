---
name: config-tracking
description: >
  **When**: category = `config`. Data contains unmatched config keys (config_key_path, file_path).
  **Task**: trace each config key in code to find **every** referencing function and register
  `(FUNCTION)-[:USES_CONFIG]->(ConfigSection)`.
  **Do NOT use for**: entity/class (`entity-analysis`), DDL (`table-tracking`), SQL keys (`sql-tracking`).
---

# Config Tracking Skill

## Core Loop

**Search → Read → Register → Follow-up → repeat.**

For each config key:

1. `config_lookup_tool(key)` → get value and file_path.
2. `bundle_search` from multiple angles:
   - full key: `bundle_search(config_key, kind="text")`
   - last segment: `bundle_search("database.url", kind="text")`
   - file name: `bundle_search("application.yml", kind="text")`
   - framework accessor: `bundle_search("getProperty", kind="text")`, `@Value`, etc.
3. For each candidate: `bundle_get_chunk` → verify real reference.
4. Register **immediately**: `graph_create_refer_to_tool("ConfigSection", config_key, evidence, source_function_id)` → USES_CONFIG edge.

## Rules

- `source_function_id` **must** be a real chunk_id from `bundle_search`.
- One key may be referenced by multiple functions — find them **all**.
- 0 hits → retry ≥2~3 times with variants.
- No evidence → no edge. Do not invent.
- Register as you find — never batch.
