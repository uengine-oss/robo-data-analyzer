# Tasks: Shared Layout

- [x] T001 Freeze Analyzer/Frontend WIP, HEADs, CodeGraph consumers, and baseline tests.
- [x] T002 Move `analyzer_settings.py` to `shared/config/settings.py`; update every consumer; old import 0.
- [x] T003 Move `runtime_events/` to `shared/events/`; update every consumer; old import 0.
- [x] T004 Move `source/file_cache.py` to `shared/files/cache.py` and `source/path_resolver.py` to `api/source_resolver.py`; remove empty `source/`; old import 0.
- [x] T005 Flatten `llm/prompt_logging/prompt_logger.py` to `llm/tracing.py`; remove empty package; old import 0.
- [x] T006 Change generated `run-output/` default/marker/docs/tests to `output/` without touching existing runtime contents.
- [x] T007 Run focused tests after every slice and full unittest/compile/prompt/structure/import checks at completion.
- [x] T008 Verify 075 behavior, old paths 0, compatibility shims 0, diff scope, and user WIP hashes.
