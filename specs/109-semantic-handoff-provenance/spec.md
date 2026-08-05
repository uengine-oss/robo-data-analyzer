# Feature Specification: Semantic Search, Handoff, and Table-Effect Provenance

**Feature Branch**: `109-semantic-handoff-provenance`

**Created**: 2026-07-30

**Status**: In Progress

**Input**: User decision: semantic retrieval uses `search_text` only; CALLS handoff passes
callee `search_text`; repetition-loop aborts are not retried; table effects distinguish
scanner authority, LLM inference, and unresolved operations without inventing precision.

## User Scenarios & Testing

### User Story 1 - One semantic retrieval truth (Priority: P1)

An analyst searching by business meaning receives candidates scored only from the detailed
`search_text` authored for retrieval. A short UI summary, code name, stereotype, table
relationship, or raw source cannot independently change semantic ranking.

**Independent Test**: Build two rows with identical `search_text` and conflicting values in
every other field. Embedding text, BM25 material, structural semantic material, cross-encoder
passage, and LLM judge body must remain identical.

**Acceptance Scenarios**:

1. **Given** a node with non-empty `search_text`, **When** its semantic materials are built,
   **Then** only normalized `search_text` is used for scoring.
2. **Given** a node with summary but no `search_text`, **When** the embedding corpus is built,
   **Then** it is not silently embedded from the summary.
3. **Given** retrieval results, **When** they are returned for display, **Then** summary and
   graph metadata may still be displayed without becoming semantic scoring input.

### User Story 2 - Loss-aware CALLS handoff (Priority: P1)

A caller receives the callee's retrieval-grade semantic interface text, while DBMS range
compaction and UI display retain the concise summary.

**Independent Test**: Give a callee deliberately different `summary` and `search_text`; the
CALLS prompt must contain only `search_text`, while `compact_body` still contains `summary`.

**Acceptance Scenarios**:

1. **Given** a completed callee summary response, **When** its caller becomes ready,
   **Then** the caller receives the callee `search_text` from that same response.
2. **Given** a callee lacking `search_text`, **When** the caller is formatted,
   **Then** no summary or one-liner fallback is substituted.
3. **Given** a DBMS parent/child range, **When** the child body is compacted,
   **Then** the concise child summary remains the compaction material.

### User Story 3 - Stop deterministic generation loops once (Priority: P1)

When the streaming repetition guard aborts a generation loop, the same semantic request is
not issued a second time.

**Independent Test**: Raise `RepetitionLoopAborted` from a mocked concern call and assert one
call, one explicit failure result, and no retry.

**Acceptance Scenarios**:

1. **Given** a repetition-loop abort, **When** concern retry policy handles it, **Then** it
   returns an explicit exhausted failure after one attempt.
2. **Given** an ordinary recoverable concern failure, **When** retry policy handles it,
   **Then** the existing bounded second attempt remains available.
3. **Given** a budget abort, **When** retry policy handles it, **Then** it continues to raise
   immediately.

### User Story 4 - Honest table-effect precision (Priority: P1)

An Example-to-Table effect records whether it is a read, write, or both; whether its exact
operation is scanner-authoritative, LLM-inferred, or unresolved; and never promotes an
unresolved operation into a false exact value.

**Independent Test**: Finalize effects for scanner-authoritative, inferred, contradictory,
and unresolved inputs and assert the persisted `access`, `op`, and `op_source`.

**Acceptance Scenarios**:

1. **Given** one scanner-authoritative operation for a function/table pair, **When** the LLM
   emits another operation, **Then** the scanner operation wins with `op_source=SCANNER`.
2. **Given** write access without an exact scanner operation and no valid LLM candidate,
   **When** the effect is finalized, **Then** it stores `access=WRITE`, `op=UNKNOWN`, and
   `op_source=UNRESOLVED`.
3. **Given** no exact scanner operation but a direction-consistent LLM candidate, **When**
   finalized, **Then** it is retained only as `op_source=LLM_INFERRED`.
4. **Given** a read-only table and a write candidate, **When** finalized, **Then** deterministic
   read access wins and the contradictory candidate is not persisted.
5. **Given** a table insight with both direct reads and writes, **When** its role is validated,
   **Then** the truthful role is `READ_WRITE`, not an unproven temporal order.

### Edge Cases

- A function both reads and writes the same table but has no single DML operation.
- An LLM omits `op`, emits a write op for a read-only table, or emits READ for a write-only table.
- A scanner adds a newly supported DML value such as TRUNCATE.
- An old consumer sees an AFFECTS_TABLE edge without `access` or `op_source`.
- A repetition abort occurs after partial streamed JSON.

## Requirements

### Functional Requirements

- **FR-001**: All semantic scoring stages MUST use `search_text` as their only node text.
- **FR-002**: Corpus eligibility for semantic search MUST require non-empty `search_text`, not
  non-empty summary.
- **FR-003**: Summary and graph metadata MAY remain in response/display structures but MUST NOT
  enter embedding, sparse semantic scoring, structural semantic scoring, CE, or judge text.
- **FR-004**: Framework CALLS handoff MUST use callee `search_text` only.
- **FR-005**: DBMS range compaction MUST continue using child `summary`.
- **FR-006**: `RepetitionLoopAborted` MUST be non-retryable at concern level.
- **FR-007**: Recoverable non-budget, non-repetition failures MUST retain the existing bounded
  retry behavior.
- **FR-008**: The LLM affected-table schema MUST remain minimal: table plus optional operation
  candidate; provenance MUST be assigned deterministically after generation.
- **FR-009**: Final table effects MUST contain `access` in `READ|WRITE|READ_WRITE`.
- **FR-010**: Final table effects MUST contain `op_source` in
  `SCANNER|LLM_INFERRED|UNRESOLVED`.
- **FR-011**: Final table effects MUST contain an exact supported op or `UNKNOWN`; unresolved
  writes MUST NOT be converted to an exact DML.
- **FR-012**: Scanner-authoritative operations and deterministic R/W direction MUST override
  contradictory LLM candidates.
- **FR-013**: `WRITE-then-READ` MUST be replaced by `READ_WRITE`; no temporal ordering may be
  asserted without ordering evidence.
- **FR-014**: AFFECTS_TABLE persistence and live graph deltas MUST expose the same
  `access/op/op_source`.
- **FR-015**: The Analyzer-to-Architect contract MUST follow
  `contracts/affects-table-v2.md`.

### Key Entities

- **Semantic Search Material**: the exact normalized `search_text` used by every semantic scorer.
- **Callee Interface Handoff**: callee ID, interface-only scope marker, and callee `search_text`.
- **Table Effect Candidate**: LLM-produced table plus optional operation candidate.
- **Final Table Effect**: Example/Table edge with deterministic access, normalized operation,
  and provenance.

## Success Criteria

### Measurable Outcomes

- **SC-001**: Changing summary/name/stereotype/raw code/table relations while holding
  `search_text` fixed changes zero semantic scoring inputs.
- **SC-002**: CALLS handoff tests prove callee summary text is absent and callee search text is
  present; compaction tests prove the inverse for DBMS child ranges.
- **SC-003**: Repetition abort invokes the concern callable exactly once.
- **SC-004**: Table-effect contract tests cover scanner, inferred, unresolved, and contradictory
  cases with zero false exact ops.
- **SC-005**: Analyzer targeted tests, full Analyzer tests, Architect contract tests, and prompt
  hash verification pass.

## Assumptions

- Spec 106 owns scanner DML recognition and is an input dependency; this feature does not edit
  its recognition rules.
- Full analysis runs rebuild the graph, so no in-place Neo4j migration is required.
- `UNKNOWN` is a valid normalized final operation, not a failure sentinel.
- Exact identifier lookup may remain a separate graph/lexical feature only when it does not
  reuse semantic scoring stages covered by FR-001.

