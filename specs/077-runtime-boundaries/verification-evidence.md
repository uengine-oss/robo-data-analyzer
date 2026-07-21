# Verification Evidence

Commands are recorded only after execution.

- Agent/LLM boundary focus suite: 75 tests passed with 4 intentional skips.
- Full Analyzer suite: 272 tests passed with 5 intentional skips; compileall and import smoke passed.
- Active old imports for pipeline-owned Agents, `llm.contracts/tools/skills`, structured Agent runner, Agent prompts, and `llm.middleware` returned 0.
- Prompt SHA256 guard reported all 27 assets byte-identical after ownership-key updates.
- Retired Agent prompt and `llm/middleware` directories were verified as empty/cache-only and removed; no compatibility shim was added.
- Fresh process `GET http://127.0.0.1:15513/` returned HTTP 200 and `{"status":"ok","service":"robo-analyzer"}`.
- CodeGraph sync completed at 374 files, 4,273 nodes, and 8,364 edges; status is up to date.
- User-deleted `.env.production`, existing `run-output/`, and unrelated Frontend/Architect/ANTLR WIP were preserved.
