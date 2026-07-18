# Feature Specification: Runtime Output and v10 Alignment

**Status**: Approved by user request on 2026-07-17

## Problem

The repository currently uses `log/` for runtime logging and stream code while `logs/` stores generated run
results. The nearly identical names obscure ownership. The implemented `system/env_catalog.py` also differs from
the approved v10 root `analyzer_settings.py`, and the tracked internal `deploy/` conflicts with the later workspace-level
deployment ownership decision.

## Requirements

- **FR-001**: Runtime log/event/progress source code MUST live under the predictive `runtime_events/` owner.
- **FR-002**: Its files MUST be named `runtime_logger.py`, `ndjson_event_stream.py`, and `processing_events.py`.
- **FR-003**: Generated analysis, prompt-capture, and search-usage results MUST use `run-output/`, not `logs/`.
- **FR-004**: The canonical setting MUST be `ROBO_RUN_OUTPUT_DIR` exposed as `SETTINGS.run_output.dir`; the old active
  setting and references MUST be zero after migration.
- **FR-005**: Existing local run results MUST be preserved by moving them to the new root before verification.
- **FR-006**: `analyzer_settings.py` MUST live at repository root as approved in v10, with no `system/` compatibility copy.
- **FR-007**: Internal `deploy/` MUST be removed only after active consumer search proves workspace/external
  deployment does not depend on it.
- **FR-008**: Runtime events, NDJSON contracts, file handlers, artifacts, prompt captures, usage files, and API
  behavior MUST remain semantically unchanged except for the explicitly renamed paths/settings.
- **FR-009**: Source, tests, scripts, README, environment templates, Docker ignores, active external runners, and
  audit evidence MUST move in the same vertical slice. Historical specs remain unchanged.

## Success Criteria

- `system.env_catalog`, `from log.`, `ROBO_LOG_DIR`, `SETTINGS.log.dir`, and active `logs/...` references are zero.
- Old `system/`, `log/`, `logs/`, and internal `deploy/` roots are absent after safe migration.
- Full unit, compile, prompt hash, live MCP, and Analyzer UI gates pass.
- A controlled test run writes all expected files only below `run-output/` and emits the same NDJSON event types.
