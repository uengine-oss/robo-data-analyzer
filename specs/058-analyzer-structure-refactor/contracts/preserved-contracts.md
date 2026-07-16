# Preserved Contracts

## Public/runtime contracts

- Root service startup and API router behavior remain callable through documented commands.
- AST JSON input shape and missing-input failure semantics remain unchanged.
- Neo4j node labels, relationship names, node IDs and writer ordering remain unchanged.
- Catalog `sample-context` and lineage interaction remains compatible.
- Search and MCP response semantics, including the tiered string response contract, remain unchanged.
- Prompt common-prefix bytes remain identical and all prompt hashes cover the final prompt set.
- Budget breaker, error stream emission and raised-failure behavior remain active.

## Internal migration contract

- Internal import paths may change only with all static, dynamic, test, script, CI, deploy and external workspace
  consumers updated in the same verified slice.
- Forwarding modules at old paths are not an accepted final state.
- Historical specs and baselines are not rewritten merely to make old-path searches zero; searches distinguish
  immutable historical references from active runtime references.

## Acceptance evidence

- Python import/compile and full unit test suite.
- Old active path/name search zero.
- Prompt byte-identical check.
- Search gold at least 29/30 and baseline graph diff within approved bounds.
- Full shopmall reanalysis, REST, MCP, UI and Architect legacy-evidence smoke.
