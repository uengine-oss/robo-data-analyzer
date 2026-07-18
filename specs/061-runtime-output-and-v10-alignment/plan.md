# Implementation Plan

1. Freeze the active producer/consumer map for configuration, runtime event modules, generated output paths,
   environment variables, scripts, docs, Docker, and external runners.
2. Move `system/env_catalog.py` to root `analyzer_settings.py` and `log/` to `runtime_events/` using history-preserving moves; rename the
   three modules and update all imports in one slice.
3. Rename the output setting to `AnalysisRunOutputSettings`, `SETTINGS.run_output`, and `ROBO_RUN_OUTPUT_DIR`; update every
   producer and consumer without a long-lived alias.
4. Preserve-move the ignored local `logs/` directory to `run-output/`, then update ignore and operator docs.
5. Remove internal deploy only after a zero-consumer check.
6. Run static old-symbol/path gates, compile, full unit and prompt contracts, a controlled output run, live MCP,
   and real UI verification. Record evidence before checking tasks complete.

## Dependency direction

`analyzer_settings.py` is a leaf configuration owner. `runtime_events/` may depend on it; pipeline, graph, LLM, search,
and API may depend on both. Neither owner may import business-step implementations.
