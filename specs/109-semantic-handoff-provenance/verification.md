# Verification: Semantic Handoff and Effect Provenance

## Automated evidence

- `python -m unittest tests.search.test_search_text_only_contract
  tests.pipeline.step7_analyze_code.test_semantic_field_independence
  tests.pipeline.step7_analyze_code.test_semantic_generality_contract
  tests.pipeline.step7_analyze_code.test_external_prompt_contract`
  - 36 passed.
- `python -m unittest tests.pipeline.step7_analyze_code.test_concern_retry_contract`
  - 11 passed, including one-attempt `RepetitionLoopAborted`.
- `python -m unittest discover -s tests/pipeline/step7_analyze_code -p "test_*.py" -t .`
  - 63 passed.
- `python -m unittest discover -s tests -p "test_*.py" -t .`
  - 546 passed, 5 skipped.
- `python scripts/check_byte_identical.py`
  - 31 prompt hashes byte-identical.

## Contract audit

- Semantic embedding, BM25, structural recall, cross-encoder, diversification novelty, and LLM
  judging read only `search_text`.
- Active Framework CALLS input contains `callee_search_texts`; no active
  `callee_summaries`/`_format_callee_summaries` reference remains.
- DBMS range compaction still uses short `summary`; the CALLS handoff does not.
- LLM affected-table schema contains only `table` plus optional candidate `op`.
- Deterministic fan-in is the only owner of `access`, final `op`, and `op_source`.
- Persisted and live AFFECTS_TABLE payloads are v2-equivalent.
- Pre-existing scanner work in specs 106–108 and `.specify/feature.json` was not reverted or
  rewritten.

## Paired consumer

Architect spec 050 verification records 25 focused hybrid tests passing, the frontend production
build passing, and the unrelated pre-existing language chokepoint failures found by its broad suite.
