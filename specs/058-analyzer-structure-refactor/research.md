# Research: Analyzer Structure Refactor

## Decision 1 — Current code overrides the old deletion table

**Decision**: Keep and relocate `shared/content_text.py` and `shared/node_registry.py`; do not recreate or delete
`module_test/` because it is absent.

**Rationale**: Current full-tree search proves three live LLM consumers for `content_to_text` and an AST loader plus
four integrity tests for `canonicalize_node_lists`. Deleting either would break active behavior. `module_test/` does
not exist in the working tree and is not tracked.

**Alternatives considered**: Follow the stale handoff deletion note. Rejected because current producer/consumer
evidence directly falsifies “consumer 0”.

## Decision 2 — Merge programming-language knowledge, with corrected rationale

**Decision**: Merge supported extensions, ontology lookup/stereotype validation, strategy detection and the adjacent
ontology YAML into the `pipeline/programming_languages` responsibility.

**Rationale**: They jointly define which source languages are accepted, how AST labels are interpreted and which
pipeline is selected. They share one change reason even though only strategy detection currently imports the extension
catalog; the earlier “all mutually import” statement was inaccurate.

**Alternatives considered**: Keep three files. Viable, but preserves a distributed language policy and makes adding a
language touch multiple discovery locations. Merge is retained because the user approved v10 and cohesion is real.

## Decision 3 — Dependency-ordered moves, no compatibility mirror

**Decision**: Move graph → LLM → pipeline → search → API/log/scripts/tests, updating every consumer in the same slice.

**Rationale**: This follows foundational dependencies outward and permits a local import/test checkpoint after each
slice. Old import packages are removed rather than retained as forwarding modules.

**Alternatives considered**: One bulk move or temporary compatibility packages. Bulk movement makes failures hard to
localize; compatibility mirrors create two truths and tend to become permanent.

## Decision 4 — Review large files by responsibility, not line count

**Decision**: Inspect the eight nominated large files and split only where independently named responsibilities,
consumers and tests exist. Record `split` or `pass` with evidence in the audit ledger.

**Rationale**: A line threshold alone creates artificial fragments. The quality goal is maintainable boundaries,
not small files.

**Alternatives considered**: Mandatory split of every file above a threshold. Rejected as contrary to cohesion and
the user's “do not force a violation” rule.

## Decision 5 — Preserve public and semantic contracts

**Decision**: Treat internal imports as replaceable but AST input, Neo4j labels/relationships, Catalog HTTP,
REST/MCP output, prompt bytes, budget breaker and run failure behavior as frozen contracts.

**Rationale**: The task is a structural refactor. Any intentional behavior change requires a separate specification
and baseline.

**Alternatives considered**: Opportunistic behavior cleanup during moves. Rejected because it prevents attribution
of regressions; only proven principle violations with focused tests may change behavior.
