# Implementation Plan: Shared Layout

## Constitution Check

- SDD precedes source edits.
- Moves use `git mv` and preserve history.
- `shared/` is admitted only for proven cross-domain infrastructure.
- No compatibility shims; producer and every consumer move in the same slice.
- Existing 075 and Frontend WIP remain intact.

## Target Tree

```text
shared/
├─ config/settings.py
├─ events/{labels,log_api,ndjson_event_stream,processing_events,run_file_handler,runtime_logger}.py
└─ files/cache.py
api/source_resolver.py
llm/tracing.py
output/                         # generated, ignored
```

## Slices

1. Freeze Git/WIP, CodeGraph impact, full tests, and hashes.
2. Move configuration and update all consumers; focused settings/import tests; old path 0.
3. Move events and update all consumers; runtime event tests; old path 0.
4. Move file cache and API resolver; file/API/pipeline tests; remove empty `source/`; old path 0.
5. Flatten LLM tracing; LLM logging tests and prompt behavior regression; old path 0.
6. Switch generated default to `output/`, including marker, ignore rule, docs and tests; preserve existing runtime data.
7. Full tests, compile, prompt hash, CodeGraph sync, active grep 0, and diff/WIP audit.
