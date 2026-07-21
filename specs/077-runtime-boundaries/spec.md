# Feature Specification: Runtime Boundaries

**Created**: 2026-07-21
**Status**: Specified

## Goal

Make every Analyzer runtime folder name state its actual responsibility. Separate autonomous Agent workflows from lower-level LLM infrastructure, and eliminate stale runtime-tree residue without changing analysis behavior.

## Requirements

- Keep `api`, `graph`, `pipeline`, `search`, `shared`, and `scripts`; their consumers and contents match their declared domains.
- Rename the outbound service adapter root `external/` to `integrations/` so it cannot be confused with analyzed external files.
- Add top-level `agents/` as the sole owner of autonomous scan, external-format, and residual-pattern workflows.
- Keep `pipeline/steps/` as deterministic stage entry/orchestration only; it invokes Agent workflows but does not own their implementation.
- Move Agent output schemas to `agents/contracts.py`.
- Move Agent-callable inspection/filesystem/validation/message code under `agents/tools/`.
- Move cross-domain safe path handling to `shared/files/paths.py`.
- Move Agent skills and their catalog to `agents/skills/`.
- Move scan/external/residual system prompts to `agents/prompts/`.
- Move the generic structured-Agent runtime to `agents/runtime.py`; keep direct LLM/YAML runners under `llm/runners/`.
- Move every `AgentMiddleware` implementation and the standard Agent stack to `agents/middleware/`.
- Keep the cache-friendly message transformation shared by direct LLM calls and Agents at `llm/filters.py`; do not leave an ambiguous `llm/middleware/` owner.
- Rename `llm/model_configs/` to `llm/configs/`; place its loader at `llm/configs/loader.py`.
- Remove redundant `llm_` filename prefixes inside the `llm` owner: `client.py`, `fanout.py`, and `messages.py`.
- Consolidate the shared summary prompt into `llm/prompts/common/`.
- Keep the Agent skill template in the single-word `agents/skills/templates/` folder and exclude it explicitly from runtime skill discovery.
- Remove only generated stale caches and empty retired package directories; do not delete user runtime output or authored data.
- Add no compatibility shims, duplicate modules, or fallback imports.

## Acceptance

Full unittest, focused Agent/LLM/scan/linking/external tests, prompt hashes, compile/import smoke, CodeGraph sync, active old-path grep, and fresh HTTP startup pass. The existing external-file feature and user-deleted `.env.production` state remain unchanged.
