# Regression Evidence: Analyzer Structure Refactor

## Pre-move baseline — commit `4a1bb126b3fe1c26c0ce54363bb4caab49863486`

| Gate | Result | Evidence |
|---|---|---|
| Python compile | PASS | `python -m compileall -q analysis_flow api agent_tools cli_scripts data_models search shared analysis_pipelines.py main.py`, exit 0 |
| unittest discovery | PASS | 42 tests, 0 failures |
| plain zero-argument tests | PASS | 51 tests, 0 failures; direct import runner |
| Prompt byte contract | PASS | `cli_scripts/check_byte_identical.py`: 23 prompts byte-identical |
| pytest command | ENVIRONMENT GAP | `.venv` has no `pytest` module; not reported as a code failure |
| Search gold 29/30 | NOT RUN | Requires the verified Neo4j gold environment and explicit database override |
| Full reanalysis | NOT RUN | Deferred until post-move comparison; destructive run safety rules apply |
| REST/MCP/UI | NOT RUN | Deferred to final integrated gate |

Expected local baseline is therefore 93 executable tests (42 unittest + 51 plain), compile success and 23 prompt
hash/byte checks. Because the two runners cover disjoint test shapes, their counts are added; unittest methods are
not re-executed by the plain-function runner.

## Slice evidence

Append one row per migration slice with exact command, pass/fail, old-reference result and any unresolved gap.

| Slice | Compile/import | Focused tests | Old active refs | Quality audit |
|---|---|---|---|---|
| system + graph | PASS | 24 tests + node-upsert helper parity PASS | `shared.config`, `shared.neo4j`, graph `data_models` = 0 | 42 files AST/content scan; relation literals 0; ledger 42 fixed |
| LLM ownership | PASS | unittest 46/46 (3 migration skips), plain 51/51, prompt 23/23, config/pricing smoke PASS | old LLM imports and active asset paths = 0 | 67 baseline files + new package markers audited; silent backup failure fixed; ledger cumulative 109 fixed |

The first LLM full-suite attempt used the system Python and failed collection because that interpreter lacks
`langchain`, `mcp`, `numpy` and `langchain_anthropic`. The authoritative rerun used `.venv/Scripts/python.exe`,
matching the baseline environment, and passed. This is recorded to prevent an environment mismatch from being
misreported as either a product regression or a successful gate.
