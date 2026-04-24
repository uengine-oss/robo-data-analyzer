Sample collected. Using this + tools, describe **this project's framework pattern and DB access pattern**.

Purpose: provide a **map** so the downstream linker can find real tables/functions on its own. **No entity enumeration / describe patterns, mechanisms, and rules.**

**Extreme compression principle**: minimum needed for the linker to design search strategy. No prose, no decoration, no restatement. Do not omit core rules. Compress each key rule into one sentence.

---

[1] Extensions: {extensions}

[2] Manifest ({manifest_name}):
{manifest_content}

[3] Config ({config_name}):
{config_content}

[4] Top import/include (from {sample_file_count} files):
{import_frequency}

[4b] import/include co-occurrence:
{import_cooccurrence}

[5] Representative file skeleton ({representative_file}):
{representative_skeleton}

[5b] Function index (file → signatures):
{function_index}

[5c] Function-name token n-grams (1-gram / 2-gram top):
{function_name_ngrams}

[5d] First 3 call symbols per function (representative file):
{call_sequences}

[5e] Function length stats: {function_length_stats}

[6] Scanner-classified resources (reference):
{resources_summary}

---

## Approach (guideline — feel free to go deeper)

1. **Step 1 — sample scan**: detect language/framework/scale from extensions, imports, signatures.
2. **Step 2 — tool verification**: confirm real roles of repeated symbols via `grep` / `read_file` / `read_function`. No guessing, body evidence required.
3. **Step 3 — compressed output**: 3 blocks below, strict limits.

≤ 10 tool calls.

---

## Output rules — extreme token compression

### Block 1 — `## Framework Pattern` (1 paragraph, 3~5 sentences)

- Identity of framework → wrapper macro/method → exception/error propagation → logging.
- Group variants (e.g. `X_TRY`/`X_TRYNJ`/`X_CATCH`) as "the X family", mention once.
- **Forbidden**: project-specific struct/file names (e.g. `xxx_io_t`), stdlib listing, "to summarize...", paragraph repetition.

### Block 2 — `### Symbol Role Classification` (table, **max 7 rows**)

Roles: **wrapper / logging·debug / error return / business call / DB access / language stdlib / constant**.

Format:

```text
| Symbol / Pattern | Role | Function | Handling |
| --- | --- | --- | --- |
| <symbol or variant group> | <role> | <≤8 words> | unwrap / exclude / extract |
```

Rules:

- **Always group variants into one row**. `PFM_TRY, PFM_TRYNJ, PFM_CATCH` → one row.
- **Constants grouped**: `RC_OK, RC_NRM, RC_ERR` → one row.
- No duplicate roles. No project-specific struct type rows.
- Description ≤ 8 words. No decoration like "performs X and records Y".
- Omit uncertain symbols.

### Block 3 — `## DB Access Pattern` (4 subsections, each `###` header, **1~3 sentences**)

Each subsection tells the linker **how to act**, not just what the pattern is.

- `### Access Mechanism` — name pattern & argument form of the DB access function/macro (1~2 sentences)
- `### Identifier Rule` — where/how the table/resource identifier appears (literal / constant / header file / XML / annotation), AND **how to extract the table name from the identifier** (e.g., "strip suffix `_<crud>####`", "trim namespace prefix", "read `@Table(name=...)` attribute")
- `### Naming Rule (CRUD)` — prefix/suffix/digit patterns, explicit mapping **`<suffix> → READS|WRITES`** so the linker can decide access type from identifier alone; one example symbol
- `### Result / Error` — return code / exception / Optional / flag, how success vs no-data vs error are told (1 sentence)

Do not repeat content across subsections. Do not re-explain a constant/macro already mentioned. Stay language-neutral — no project-specific struct/function names outside the one example allowed in `### Naming Rule`.

---

## Output example (match this length)

```markdown
## Framework Pattern

Custom XFM framework. Business calls are wrapped by the `XFM_TRY` family (TRY/TRYNJ/CATCH); non-`RC_OK` returns propagate immediately to the caller. Errors are return-code based; no try/catch. Logging via `XFM_DBG` / `PRINT_*` macros.

### Symbol Role Classification

| Symbol / Pattern | Role | Function | Handling |
| --- | --- | --- | --- |
| XFM_TRY, XFM_TRYNJ, XFM_CATCH | wrapper | return-value check / propagation | unwrap |
| XFM_ERRC, XFM_ERRS | error return | set code / record message | exclude |
| XFM_DBG, PRINT_* | logging | debug output | exclude |
| RC_OK, RC_NFD, RC_ERR | constant | return status codes | exclude |
| xdbio | DB access | DB call with literal id | extract |
| memset, strncpy | stdlib | C stdlib | exclude |

## DB Access Pattern

### Access Mechanism
Single wrapper `xdbio(id, in_ptr, out_ptr)`. Arg 1 = string id, args 2/3 = in/out struct pointers.

### Identifier Rule
String literal embedded directly in the first arg of `xdbio`. No constants or XML. **To extract the table name: take the literal and strip the trailing `_<crud>####` suffix.** Example: `"user_tbl_s0024"` → table `user_tbl`.

### Naming Rule (CRUD)
Identifier suffix → access type: `_s####` → **READS** (SELECT), `_i####` / `_u####` / `_d####` → **WRITES** (INSERT / UPDATE / DELETE). Example identifier: `dbio_user_i0001` → table `dbio_user`, WRITES.

### Result / Error
Return `RC_OK` = success, `RC_NFD` = no data, otherwise error. Details via `xdbio_errno()` / `xdbio_errstr()`.
```
