# Run manifest contract

`<resolved-run-directory>/manifest.json` is created before graph mutation or an LLM call.

`<resolved-run-directory>` is the established flat `logs` directory when no output is supplied, or
the exact explicitly supplied `run-output` directory. Product code does not append a run-ID child and
does not create an automatic backup. Operators select a fresh explicit directory when isolation is
required.

Required fields:

```json
{
  "schema_version": "1.0",
  "run_id": "<unique>",
  "started_at": "<UTC ISO-8601>",
  "corpus_root": "<resolved path>",
  "source_root": "<resolved path>",
  "ddl_root": "<resolved path or null>",
  "strategy": "framework|dbms",
  "locale": "<BCP-47-like locale>",
  "neo4j_database": "<resolved non-system database>",
  "run_output_root": "<resolved exact selected directory>",
  "run_dir": "<same resolved exact selected directory>",
  "llm_config": "<non-secret config name>",
  "prompt_tree_sha256": "<sha256>"
}
```

The prompt hash covers relative paths and bytes of all active prompt files in deterministic path order.
Passwords, API keys, tokens, and datasource credentials are forbidden.

The manifest's input identity fields never change. A later finalization may add status, completion time,
and aggregate counts by atomic replacement inside the same run directory.
