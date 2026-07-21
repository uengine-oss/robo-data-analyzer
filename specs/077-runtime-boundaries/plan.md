# Implementation Plan: Runtime Boundaries

## Audit Result

- `api`: keep; public HTTP/MCP boundary only.
- `graph`: keep; Neo4j schema, nodes, edges, writers, and ownership only.
- `pipeline`: keep; workflow orchestration and numbered domain steps only.
- `search`: keep; corpus, query, ranking, and search facade only.
- `shared`: keep; proven multi-domain config/events/files infrastructure only.
- `scripts`: keep; operator entrypoints only.
- `external`: rename to `integrations`; it is an outbound Catalog adapter, not analyzed external-file logic.
- `agents`: create as the visible owner of autonomous Agent workflows, contracts, tools, prompts, skills, middleware, and structured runtime.
- `llm`: retain only model invocation infrastructure: client, configs, direct runners, usage, embedding, fanout, message normalization, shared message filters, tracing, and non-Agent rule prompts.
- `pipeline/steps`: retain deterministic stage entrypoints; move scan/external/residual Agent implementations to `agents`.
- `llm/model_configs`: rename to `llm/configs`; configuration assets and loader share one owner.
- `tests`: mirror final runtime owners.

## Slices

1. Freeze full folder audit and CodeGraph impact.
2. Create the top-level `agents` owner and move real Agent workflows plus their contracts/tools/prompts/skills/middleware/runtime; focused scan/external/residual tests; old path 0.
3. Normalize the remaining LLM config/root filenames and hashes; focused loader/client/runner tests; old path 0.
4. Rename outbound adapters to `integrations`; focused Catalog integration tests; old path 0.
5. Remove verified cache-only retired directories.
6. Run full tests, prompt hashes, compile/import, CodeGraph, startup, and WIP audit.
