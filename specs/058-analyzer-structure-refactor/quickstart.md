# Quickstart: Refactor Validation

Run commands from `project/robo-data-analyzer` unless noted.

1. Confirm the baseline and clean starting point:
   - `git rev-parse HEAD` equals the recorded baseline before R1 changes.
   - `audit-baseline.txt` reports 503 paths and matches the baseline tree.
2. After each migration slice:
   - run `python -m compileall -q` on the moved owner and its consumers;
   - run the slice's focused tests;
   - search active code/config/scripts/CI/deploy for old imports and paths.
3. After LLM assets move:
   - run the byte-identical prompt checker and confirm every final prompt is hashed.
4. After all moves:
   - run the full Analyzer unit suite;
   - run the 057 search gold runner and require at least 29/30;
   - execute one clean shopmall reanalysis and compare against the stored graph/meaning baseline;
   - start REST/MCP and verify central UI plus Architect evidence retrieval.
5. Reconcile evidence:
   - baseline rows = 503;
   - new R1 files all have rows;
   - `pending` rows = 0;
   - old active references = 0;
   - required regression gates all `passed`.

Do not claim completion for a command that was not actually run. Record unrelated pre-existing failures separately
with reproduction evidence; a failure in a changed contract remains an R1 blocker.
