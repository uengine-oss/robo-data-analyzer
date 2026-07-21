# Graph Contract

## Labels

Stable labels added to schema, constraints and ownership migration allowlist:

`PROJECT_FILE`, `SQL_STATEMENT`, `CONFIG_SECTION`, `ARTIFACT_CHUNK`, `CHUNK`, `DOCUMENT`,
`DOC_SECTION`, `DOC_CHUNK`, `UNRESOLVED_REF`, `CANDIDATE_LINK`.

Each node is merged by `{id, graph_owner:'analyzer'}` and receives no empty-string/None properties.
Every label has an ID uniqueness constraint scoped by label. Dynamic label selection is from a closed constant map.

## Relationship types

Single truth in `graph/schema/rel_types.py`:

`CALLS_SQL`, `USES_CONFIG`, `REFER_TO`, `MENTIONS`, `HAS_CHUNK`, `HAS_PART`, `HAS_SECTION`.

READS/WRITES are reused unchanged. Relationship Cypher is selected from a closed map; no user-provided relation
is interpolated. Both endpoints must match Analyzer-owned nodes. Missing endpoint count is an error, not success.

## Evidence

- Direct code links retain `evidence_file`, `evidence_line`, optional `via_const`.
- Artifact FQN links target the existing Analyzer `Module` class/file entity and retain `matched_fqn`; the
  linker never fabricates a class-shaped `CodeNode`. Direct code-key use remains `CodeNode→ARTIFACT_CHUNK`.
- Document mentions retain exact `matched_text`.
- Part/section relationships retain deterministic order.
- SQL/table links retain existing DML evidence.

## Idempotency

Replaying identical node/edge batches produces identical ID and relationship triples. Evidence property updates may
replace identical semantic values but cannot create parallel duplicate relationships.

## Fail-closed rules

- Unknown labels, relation classes, relationship constants or blank endpoints raise a contract error.
- Invalid line/order/score values are rejected at dataclass/builder boundary.
- Candidate links are nodes only and cannot be emitted through relationship writer.
- graph owner is mandatory on create, match, wipe and verification queries.

## Tests

- ID namespace and collision tests.
- Writer query ownership/closed-label tests.
- Neo4j integration with duplicate replay and missing endpoints.
- Persisted edge versus NDJSON wire equality for every relationship.
