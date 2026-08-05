# Research: Semantic Handoff and Provenance

## Decisions

### D1 — Search text is the sole semantic scoring material

The current pipeline embeds a concatenation of label, name, stereotype, table relations,
summary, and search text; BM25 and structural recall use name, summary, and raw code; CE and
the LLM judge again mix summary and search text. That makes `search_text` non-authoritative and
duplicates lossy/precise representations.

Decision: retain those fields for display and graph traversal, but make every semantic scorer
consume exactly normalized `search_text`.

### D2 — CALLS and range compaction have different information needs

CALLS needs a detailed semantic interface to understand a callee's purpose and observable
behavior. Range compaction needs a short replacement for removed child source. Using one field
for both either loses detail or expands parent prompts.

Decision: CALLS uses `search_text`; DBMS PARENT_OF compaction uses `summary`. Both fields are
produced by the same summary response, so scheduler release timing does not change.

### D3 — Repetition abort is deterministic, not transient

The repetition callback already distinguishes a generated loop from transport failure. Reissuing
the identical concern after this exception can reproduce the same loop and double cost.

Decision: `RepetitionLoopAborted` is immediately exhausted at concern level. Transport retry
policy remains separately owned by the runner.

### D4 — Provenance is post-generation metadata

Adding access and provenance to the LLM response schema would spend tokens and ask the model to
author facts already known by deterministic R/W linking. It would violate schema restraint.

Decision: the LLM emits `{table, op?}`. A shared deterministic serializer finalizes:

1. scanner single-op authority;
2. deterministic read-only direction;
3. direction-consistent LLM candidate;
4. otherwise `UNKNOWN/UNRESOLVED`.

### D5 — UNKNOWN is explicit and persisted

Neo4j relationship identity currently includes `op`; nullable relationship keys are awkward and
can collapse or fail matching. An explicit `UNKNOWN` retains stable identity and makes consumers
handle uncertainty intentionally.

Decision: final persisted op is never null. Only the LLM candidate is optional.

### D6 — Access and role are not operation

R/W direction is structural, table usage role is semantic, and DML operation is exact behavior.
The existing `WRITE-then-READ` claims ordering that validation never proves.

Decision: use `READ_WRITE`. Preserve lookup/append-only/upsert roles, but never use role as exact
DML authority.

### D7 — Backward consumer fallback is read-only compatibility

Architect may encounter older edges in tests or existing graphs. It can derive legacy access
from op (`READ` versus other values), but new analyzer output always carries explicit provenance.

Decision: consumer normalization is additive and does not rewrite Analyzer graph data.

