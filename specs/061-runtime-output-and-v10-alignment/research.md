# Research

- Approved v10 places `analyzer_settings.py` at repository root, but spec 058 implementation moved it to `system/`.
  The user noticed the mismatch; the approved tree is restored by this spec.
- `log/` contains executable cross-cutting code: standard logger, NDJSON event stream, and processing event scope.
  `logs/` contains generated analysis artifacts, prompt captures, usage, health, and search usage. They are distinct
  responsibilities with confusing names.
- `runtime_events/` describes the code responsibility; `run-output/` describes disposable/generated filesystem
  results. File names are expanded so searches do not require package context.
- Existing run output is ignored but valuable verification evidence, so it is moved rather than deleted.
- `deploy/k8s.yaml` is tracked internally. Removal requires a final active consumer search because the later Goal
  assigns deployment to an external owner.
