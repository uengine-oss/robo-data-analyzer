## Task

Trace usage in code and register graph relationships via tools.

**Order (strict)**:

1. Call `list_skills()`.
2. Call `read_skill(name)` for the category below. Never skip.
3. Follow that skill's "category handling" exactly.
4. Process every item in `data:` — then exit.

---

category: {category}

data:
{data}
{profile_hint}
---

**Notes**:

- Even if `items` is `[null]` or empty, do NOT exit. Use the profile hint as the starting map (especially for `profile_only`).
- If `context.existing_fks` / `context.xml_entity_hints` present, follow skill guidance.
- Every tool call's `source_function_id` MUST be a real chunk_id from a `bundle_search` result.
- **Register each relationship immediately as you find it — never batch.**
