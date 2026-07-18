# Regression Evidence: Analyzer Structure Refactor

> The pre-move and slice commands below are immutable historical evidence.
> They are not current run instructions. The present final layout is governed
> by spec 062 and `D:/work/robo/세서비스-최종트리.md`; the current authoritative
> suite is `python -m unittest discover -s tests -p "test_*.py" -t .` (194/194).

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
| pipeline policy + models | PASS | unittest 46/46 (3 migration skips), direct policy/model smoke PASS | old language-policy and analysis-model imports = 0 | 9 baseline files audited; policy/model SSOT merges; ledger cumulative 118 fixed |
| pipeline execution | PASS | unittest 46/46 (3 migration skips), plain 51/51 | old `shared.workflow` imports and active paths = 0 | 10 baseline files audited; silent artifact/cleanup failures fixed; ledger cumulative 128 fixed |
| pipeline steps + helpers | PASS | unittest 46/46 (2 migration skips), plain 51/51 | old flow, policy-helper and dynamic asset paths = 0 | 83 baseline files audited; discovery/path regressions and silent lineage-index failure fixed; ledger cumulative 211 fixed |
| search + API + log + scripts | PASS | compile PASS; unittest 48/48 (0 skips); plain 51/51; prompt 23/23; public imports and 4 external runners PASS | removed roots and active old imports/paths = 0 | 503 baseline + 43 final new files audited, pending 0; final verdicts: fixed 302, deleted 1, no-violation 243; fixed moved env default-data path, eval gold/import placement, stale runtime docs and Pydantic v3 warning |

## Nominated large-file responsibility review

The approved v10 handoff nominated eight files for an explicit split decision. Line count alone was not used as a
reason to split: each file was reviewed by its public entry points, state mutations, external I/O and failure
boundary. All eight are `no-violation`; their internal helpers already isolate suboperations while the file retains
one lifecycle or adapter responsibility. Splitting them further would create cross-file state threading without
removing a second reason to change.

| Final file | Decision | Responsibility evidence |
|---|---|---|
| `pipeline/steps/step2_load_ast/antlr_json_reader.py` | no-violation | One ANTLR JSON-to-domain adapter; node builders share traversal identity and source-range state. |
| `pipeline/steps/step2_load_ast/reference_linker.py` | no-violation | One deterministic REFERENCES/INIT_BY linking pass over the loaded AST identity map. |
| `pipeline/steps/step6_analyze_code/analyze_code_step.py` | no-violation | One step-6 orchestration lifecycle; planning, fan-out and persistence are delegated to adjacent modules. |
| `pipeline/steps/step6_analyze_code/batch_analyze.py` | no-violation | One DBMS batch-analysis lifecycle with shared request/result correlation and failure propagation. |
| `pipeline/steps/step6_analyze_code/three_split_llm_fanout.py` | no-violation | One framework three-call fan-out/fan-in contract whose byte-identical prefix and result join change together. |
| `pipeline/steps/step1_parse_ddl/ddl_parser.py` | no-violation | One multi-dialect DDL-to-schema adapter; dialect branches produce the same graph-domain contract. |
| `search/ranking/retriever.py` | no-violation | One retrieval/ranking algorithm; anchor, support and hop helpers share a single candidate-set invariant. |
| `log/logger.py` | no-violation | One logging boundary configuring formatters, handlers and structured step/error emission as a unit. |

The first LLM full-suite attempt used the system Python and failed collection because that interpreter lacks
`langchain`, `mcp`, `numpy` and `langchain_anthropic`. The authoritative rerun used `.venv/Scripts/python.exe`,
matching the baseline environment, and passed. This is recorded to prevent an environment mismatch from being
misreported as either a product regression or a successful gate.

The first final-tree discovery command omitted `-t .`, so unittest treated `tests/` as the import root and its
`api`, `pipeline` and `search` mirror packages shadowed the product packages. The authoritative command is
`python -m unittest discover -s tests -t . -p "test*.py"`; it collected and passed 48 tests with zero skips.

## Final live regression — 2026-07-16/17

| Gate | Result | Evidence |
|---|---|---|
| Clean shopmall reanalysis | PASS | `goal-verify/output/runs/r1-analyzer-refactor/shopmall-reanalysis-20260716-235822.ndjson`: 4,003,852 bytes, 439.9 s, invalid JSON 0, error 0, complete 1, stream_end 1; 12 source files, 1,780 CodeNodes, 124 embedded functions, 22 tables and 153 columns |
| Full unit/import | PASS | `.venv/Scripts/python.exe -m unittest discover -s tests -t . -p "test*.py"`: 50 tests, 0 failures, 0 skips; public/settings and asset-path move regressions included |
| Prompt byte contract | PASS | `scripts/llm_maintenance/check_byte_identical.py`: 23 prompts byte-identical |
| Live REST search | PASS | `semantic-rest-settings-fix.ndjson`: error 0, 1 cluster, 20 nodes, 18 nodes with rules and 11 with table evidence |
| Search gold | PASS | `sweep_057_p5.py`: adopted P5 h3/r2 = 30/30, average 15.6 nodes; noise 11 remains an explicit R2 improvement target, not an R1 semantic regression |
| Graph/meaning battery | PASS | `run.ps1 -Suite graph`: e2e 14/14; source functions 124/124, tables 22/22, FK 11/11, CALLS miss/extra 0, recursive self-loop 1/1, REFERENCES miss/extra 0 across 124 functions, INIT_BY 7/7, orphan/duplicate edges 0 |
| Quality/diff/probe | PASS | domain terms max 8, duplicate rule statements 0, glossary 159; duplicate IDs 0; AFFECTS_TABLE 271 with bad targets 0; known source-vs-graph R/W boundary mismatches remain 2 |
| Live MCP transport | PASS | `verify_mcp_transport.py`: streamable-HTTP initialize/list/call succeeded; cluster_retrieve returned 1 cluster, 20 nodes, 4 rules and 2 table-evidence rows |
| Architect provenance | PASS | `verify_052.py`: PRO-001 intent SSE contained one cluster query, one 21-node legacy reference and one chain marker; persisted `legacyReferences` matched; corrected `verify_052_quality.py` reports source mismatch 0 |
| Central UI/Architect strict smoke | PRE-EXISTING FAILURES, R1 behavior verified | Analyzer default IF/PARENT_OF=0; show-hidden expansion IF/PARENT_OF=1 with canvas 5 nodes/7 relationships; restore=0. `product_stock` projection expanded from 1/0 to 19/25, every displayed node was a true table 1-hop and every displayed relationship matched the loaded Neo4j graph by id/endpoints/type (`HAS_COLUMN`, `READS`, `WRITES`, induced `CALLS`). Architect proposal/chip rendering succeeded. Strict runner still reports the baseline optional text2sql alarm SSE 500 and no-binding Figma status 404; these are outside Analyzer structure paths and remain blockers for the final R3 zero-console-error gate. |

The first final gold command was killed by its 304-second shell limit while two Python launcher/worker processes
remained. Both command-line-matched runner processes were terminated, and the authoritative unbuffered rerun
completed normally in 91.9 seconds. No result from the timed-out attempt is counted.

Live execution exposed two move-specific failures that import-only checks had missed. Three pipeline modules built
asset paths relative to their new nested files, and the search move first retained `from search import llm`, then
accidentally rewrote `ENV.search.llm_config` as though the field were a module path. The fixes establish one
`LLM_PACKAGE_DIR` asset root, use explicit `search.query.query_llm` imports, preserve the `SearchSettings` SSOT,
and add executable path/settings regression assertions. Final active old-path search is zero.

The verified R1 implementation was committed as `eebebc76cd90aacf0a1808c94a26b29a291c4bdb` and pushed to
`origin/main`; local and origin hashes matched after a fresh fetch. The final task/evidence closure is recorded in
the immediately following documentation commit.
