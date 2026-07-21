# Verification Quickstart

All commands run from `D:/work/robo/project/robo-data-analyzer` unless stated otherwise.

## Baseline and full unit

```powershell
git status --short --branch
.venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py" -t .
.venv\Scripts\python.exe -m compileall -q api graph llm pipeline runtime_events search source
```

## Focused contracts

```powershell
.venv\Scripts\python.exe -m unittest tests.graph.test_external_graph_contract
.venv\Scripts\python.exe -m unittest tests.pipeline.execution.test_graph_delta_contract
.venv\Scripts\python.exe -m unittest tests.pipeline.test_dbms_pipeline_contract
```

## Active old-path audit

Search runtime/tests/docs/scripts but exclude immutable historical specs and bytecode:

```powershell
rg -n --hidden --glob '!specs/**' --glob '!**/__pycache__/**' --glob '!.git/**' `
  'step3_scan_project|step4_linking|step5_build_context|step6_analyze_code|step7_post_processing' .
```

At final completion the old step patterns above are zero in active files. Search separately for prohibited copies:

```powershell
rg -n --hidden --glob '!specs/**' --glob '!**/__pycache__/**' `
  'java_functions|graph_io|llm_frentis|from xlink|import xlink|filetype|format_skill_agent|has_xml_mapper' .
```

## Playground truth suites

Run from `D:/work/robo/_playground/xlink` against its isolated fixtures only:

```powershell
$env:PYTHONIOENCODING='utf-8'
.venv\Scripts\python.exe verify\all.py
```

Do not run parser regeneration without its `_ast_env` isolation contract.

## Live Analyzer

Use `robo-workspace` analyzer profile and its actual ports:

```powershell
cd D:\work\robo\robo-workspace
.\robo.cmd doctor analyzer
.\robo.cmd up analyzer
.\robo.cmd status analyzer
```

Capture run ID, graph counts/fingerprint, REST/MCP responses and logs in `verification-evidence.md`. Do not wipe shared
data or use Architect submodule sources.

## Frontend

Run only after recording WIP and target hashes. Use repository scripts from current package.json, then Playwright
against the actual Analyzer UI. Verify default hidden, show-all, exact endpoints and console/network error 0.
