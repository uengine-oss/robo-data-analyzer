# Next official reanalysis and delta adjudication

This procedure is executable in the current session, but only after the generalized fixes, prompt
audits, Codegraph review, multilingual/multidomain contracts, and full local regressions all pass.
The official analysis must use a fresh explicitly selected output directory. That directory is the
run root itself; Analyzer must not add a child directory or create an automatic backup.

## Acceptance targets

Do not combine fields into one score.

### Graph structure — exact 100%

- output node and relationship identity;
- required input→output node cardinality;
- Rule/Example/Question ownership relationships;
- graph-schema and fixed-scope node/relationship counts.

Any mismatch in graph topology or cardinality blocks acceptance even if natural-language quality is
high.

Source/gold hashes, non-empty adjudication evidence, ontology membership, and prompt/response manifest
traceability are binary preservation/audit prerequisites. They are verified exactly, but are not
reported as a "100% semantic accuracy" score.

### Semantic quality contracts

Natural-language fields are judged by semantic equivalence or sufficient similarity, not byte
identity. Coverage and faithfulness remain separate.

- Target band: 80–90% Coverage and 80–90% Faithfulness for each applicable natural-language field in
  the fixed representative scope. Higher is welcome, but implementation must not distort general
  behavior to chase 100%.
- Direction, negation, exception, boundary, discriminating value, fallback, normal/error return,
  dead/commented-code status, and ownership disagreements are always failures. A field does not pass
  merely because its aggregate exceeds 80%.
- Unsupported additions are reported separately; they are not hidden inside coverage.
- `stereotype` must be one of the exact ontology labels; its correctness against the gold is a
  classification-quality percentage with the same 80–90% target, not a 100% requirement.

## One official run

1. Create a local profile outside Git. It references the corpus; it does not copy or move it.

   ```yaml
   name: official-framework-run
   data_dir: D:/corpora/fixed-corpus
   strategy: framework
   neo4j_database: analysis_test
   locale: ko
   run_output: D:/analysis-runs/official-framework-run-20260727
   ```

2. Confirm that the selected Neo4j database is disposable for Analyzer-owned graph data. The
   Analyzer wipes its owned graph at run start. Never rely on an implicit database default.
3. Run from the Analyzer repository:

   ```powershell
   .\.venv\Scripts\python.exe scripts\analyze_data_dir.py `
     --profile "D:\local-profiles\official-framework-run.yaml"
   ```

4. Read the printed preflight. It must show the intended `source`, optional `ddl`, strategy, database,
   locale, and run-output root before mutation.
5. Record the emitted run ID. All evidence must exist directly under the exact selected directory:

   ```text
   <run-output>/
     manifest.json
     llm-prompts/
     usage.jsonl
     usage_summary.json
   ```

6. Validate the manifest against the intended corpus, database, strategy, locale, and prompt-tree
   hash. Do not rename the run directory or copy its prompt files over another run.

## Post-run structural validation

Use the existing scratchpad harness only; do not add adjudication assets to the product repository.

1. Re-extract packets with function-scoped Example ownership:

   ```cypher
   OPTIONAL MATCH (r)-[:HAS_EXAMPLE]->(e:EXAMPLE)
   WHERE e.id STARTS WITH n.id + '::'
   ```

2. Prove graph and preservation prerequisites before semantic review:
   - fixed gold counts/hashes unchanged;
   - the authorized comment-corrected source has the recorded file/line counts and executable-code
     hashes matching its scratchpad backup;
   - selected graph IDs and expected node/relationship cardinalities exact;
   - no foreign Example through a shared Rule;
   - every prompt record has a target ID;
   - each final field can be traced to raw/parsed response, merge/post-processing, and write result.
3. If graph topology/cardinality or a preservation prerequisite fails, stop semantic scoring and
   classify the pipeline stage first.

## Failure-first delta adjudication

1. Freeze the prior ledgers and their SHA-256 values. Never overwrite them.
2. Start with all prior failures from `failure-evidence-ledger.jsonl`, grouped by field and risk axis.
3. For each prior failure, compare the new final output against the same fixed gold and source evidence.
4. Reuse an old verdict automatically only when all are exact:
   - representative ID, field, gold/source evidence, and evidence coordinates are unchanged;
   - the final output's canonical serialized value is byte-identical;
   - ownership/topology is unchanged;
   - the verdict contract version is unchanged.
5. A changed natural-language string is not an automatic failure and not an automatic pass. Judge its
   meaning against the evidence. A byte-different but semantically equivalent result may pass.
6. A byte-identical old failure may reuse the failure verdict. A byte-identical old pass may reuse the
   pass only if the evidence and contract version are also unchanged.
7. After prior failures, review changed outputs among prior passes, then run the aggregate invariant
   checker.
8. Report field-level Coverage/Faithfulness and stereotype classification accuracy separately. Do not
   publish a single combined “accuracy” or imply that meaning fields must reach 100%.

## Cache reuse

The response cache is reusable only when its existing full key remains exact: rule name, assembled
system prompt, assembled user content, model, token limit, and response schema. Prompt, source,
strategy, locale, or schema changes must produce a different key. Never force cache reuse by copying
old files or weakening the key.

Adjudication reuse is independent of LLM response-cache reuse and follows the stricter verdict rules
above.

## Rollback and preservation

- Do not edit `.env` to switch corpus/database/output for this run.
- Do not move, delete, or modify fixed gold. Do not make further source changes during the run.
- Do not reset, stage, or commit unrelated dirty WIP.
- If the run fails, retain its unique run directory and manifest as failure evidence. Start a new run
  with a new ID after fixing the cause.
