# Active Consumer Map

Historical `specs/*` text is immutable history and excluded from active old-path zero. Runtime, tests, README and
scripts are active consumers.

## step3_scan_project → step4_scan_project

- `pipeline/strategies.py`
- `pipeline/execution/workflow_state.py`
- `agents/tools/messages.py`
- internal imports within the step
- `tests/pipeline/step3_scan_project/*`
- `tests/structure/test_refactor_contract.py`

## step4_linking → step5_linking

- `pipeline/strategies.py`
- `pipeline/execution/{workflow_state,artifact_writer}.py`
- scan step imports of verification tools
- analyze/postprocessing/runtime logger imports
- internal imports within link_calls/link_tables
- `tests/pipeline/step4_linking/*`
- `tests/structure/test_refactor_contract.py`

## step5_build_context → step6_build_context

- `pipeline/strategies.py`
- `pipeline/execution/{workflow_state,artifact_writer}.py`
- `llm/runners/{invocation,yaml_llm_runner}.py`
- analyze range/body imports
- `tests/pipeline/step5_build_context/*`
- `tests/structure/test_refactor_contract.py`

## step6_analyze_code → step7_analyze_code

- `pipeline/strategies.py`
- graph example writer, LLM output/runners, pipeline models, context builder
- postprocessing and runtime logger imports
- `README.md`
- `tests/pipeline/step6_analyze_code/*`, pause/context/domain tests
- `tests/structure/test_refactor_contract.py`

## step7_post_processing → step8_post_processing

- `pipeline/strategies.py`
- LLM runners, artifact/model imports
- `README.md`
- `scripts/{reembed_graph,resynthesize_glossary}.py`
- graph ownership/postprocessing/catalog tests
- `tests/pipeline/step7_post_processing/*`
- `tests/structure/test_refactor_contract.py`

## Skill consumers

- `agents/skills/catalog.py`
- `agents/tools/inspection.py`
- `agents/scan.py` and `pipeline/steps/step4_scan_project/scan_codebase_step.py`
- README and skill tests

## Graph/stream consumers

- graph schema, node writers, edge writers, constraints, ownership tests
- `pipeline/execution/{workflow_state,graph_delta_stream,artifact_writer}.py`
- pipeline step3/step5 producers and step6 consumer
- Frontend `analyzer.ts`, `graphVisibility.ts`, `nodeRelationships.ts`

## External consumers checked at each move

- workspace/deploy/goal-verify scripts containing Analyzer internal paths
- Architect pinned submodule is not modified; its current pointer/WIP is preserved
- Python dynamic imports and string paths
- README command examples and structure tests
