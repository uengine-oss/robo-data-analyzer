# Analyzer → Architect AFFECTS_TABLE v2 Contract

## Producer

`robo-data-analyzer` persists:

```text
(Example)-[:AFFECTS_TABLE {
  access: "READ" | "WRITE" | "READ_WRITE",
  op: "READ" | "INSERT" | "UPDATE" | "DELETE" | "MERGE" | "TRUNCATE" | "UNKNOWN",
  op_source: "SCANNER" | "LLM_INFERRED" | "UNRESOLVED"
}]->(Table)
```

## Semantics

- `access` is safe for read/write-side-effect classification.
- `op=UNKNOWN` means an exact DML MUST NOT be invented downstream.
- `op_source=SCANNER` is authoritative.
- `op_source=LLM_INFERRED` is a semantic hint, not structural truth.
- `op_source=UNRESOLVED` requires access-only fallback.

## Consumer Rules

1. Aggregate table candidacy may use `access in {WRITE, READ_WRITE}` even when op is UNKNOWN.
2. Event verb selection may use exact scanner op as primary evidence.
3. An LLM-inferred exact op may be shown as inferred and used only as a weak hint.
4. UNKNOWN must never map mechanically to Created/Updated/Deleted.
5. READ-only effects must not enter collections named `writes`.
6. Legacy edges without `access` derive access from op for compatibility only.

## Compatibility

The graph is rebuilt per analysis run; no producer migration is needed. Architect keeps a
read-only legacy fallback so fixtures and pre-rebuild graphs fail soft without erasing
provenance from new edges.

