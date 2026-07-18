# Verification

1. Assert active old imports, settings, paths, and roots are zero.
2. Import `analyzer_settings` and all three `runtime_events` modules from a clean Python process.
3. Run the full Analyzer unit suite, compileall, and prompt hash checks.
4. Execute a controlled analysis/output test and assert analyzer/error logs, artifacts, usage, prompt captures, and
   health output are under `run-output/` only.
5. Restart Analyzer with the official workspace runner; rerun live MCP list/detail and real UI graph checks.
