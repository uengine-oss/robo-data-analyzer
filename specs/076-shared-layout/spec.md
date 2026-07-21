# Feature Specification: Shared Layout

**Created**: 2026-07-21
**Status**: Specified

## Goal

Make cross-domain infrastructure visually explicit without creating a generic utility dump. Only code consumed by multiple top-level runtime domains belongs under `shared/<domain>/`. Folder names introduced by this change are single words.

## Requirements

- Move Analyzer configuration to `shared/config/settings.py`.
- Move the cross-domain runtime event subsystem to `shared/events/`.
- Move the run-scoped file cache to `shared/files/cache.py`.
- Keep the API-only source path resolver with its API owner as `api/source_resolver.py`.
- Flatten the one-file `llm/prompt_logging/` package to `llm/tracing.py`.
- Change the default generated directory from `run-output/` to `output/`; do not move or delete an existing `run-output/` directory.
- Do not move graph schemas, pipeline models, LLM domain code, numbered pipeline steps, or user WIP into `shared/`.
- Do not add compatibility packages, import aliases, fallback paths, or duplicated implementations.
- Active code, tests, README, scripts, and current specs must have zero references to removed module paths and the literal generated path `run-output/`.

## Acceptance

1. Full Analyzer unittest, compileall, prompt hash, structure contracts, and import smoke have no new regression.
2. `shared/config`, `shared/events`, and `shared/files` have explicit dependency direction and no business logic.
3. Existing 075 external-file behavior and Frontend contract tests remain unchanged.
4. Existing `run-output/` contents are untouched; new default output resolves to `output/analysis/latest`.
5. CodeGraph and text search find no active consumers of `analyzer_settings`, `runtime_events`, `source.file_cache`, `source.path_resolver`, or `llm.prompt_logging`.
