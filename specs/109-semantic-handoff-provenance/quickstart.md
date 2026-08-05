# Quickstart Verification

Run from `project/robo-data-analyzer`:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.search.test_search_text_only_contract
.\.venv\Scripts\python.exe -m unittest tests.pipeline.step7_analyze_code.test_semantic_field_independence
.\.venv\Scripts\python.exe -m unittest tests.pipeline.step7_analyze_code.test_concern_retry_contract
.\.venv\Scripts\python.exe -m unittest tests.pipeline.step7_analyze_code.test_rw_table_filter
.\.venv\Scripts\python.exe -m unittest tests.graph.test_affects_table
.\.venv\Scripts\python.exe scripts\check_byte_identical.py
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py" -t .
```

Then run the paired Architect verification in
`project/robo-architect/specs/050-analyzer-effect-provenance/quickstart.md`.

Manual contract audit:

1. `rg "WRITE-then-READ" pipeline llm tests` returns zero.
2. Active Framework prompt and code use `callee_search_texts`, not `callee_summaries`.
3. Every persisted/live AFFECTS_TABLE effect carries `access`, `op`, and `op_source`.
4. Semantic scoring functions contain no summary/name/code/table-relation concatenation.

