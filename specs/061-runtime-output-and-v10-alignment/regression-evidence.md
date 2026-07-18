# Regression Evidence

Verified 2026-07-17 against the current worktree.

- Active consumer search: old settings import, `log.*` imports, old output setting/path, and internal deploy consumers: 0.
- Existing ignored results preserved: `run-output/{analysis,llm-prompts,search}`; analysis log, error, health,
  tool-event, usage, and usage-summary files retained.
- Compile: `compileall` passed for product, tests, scripts, and root entry points.
- Structure contracts: 11/11 passed, including 503 baseline-ledger reconciliation and old-root absence.
- Full unit discovery from repository root: 66/66 passed.
- Prompt contract: 26/26 byte-identical.
- Live MCP after official restart: 1 cluster, 22 nodes, 20 rules, 21 table evidence, 3 details,
  159 relationships, 26,325 list bytes; PASS.
- Real Analyzer UI: structural nodes hidden→shown→restored, expanded table projection 19 nodes/25 exact source
  relationships, console errors 0, HTTP errors 0; PASS.
- Invalid verification command retained as a warning: `unittest discover -s tests` shadows product packages with
  `tests/pipeline`, `tests/graph`, and `tests/search`; use repository-root discovery.
