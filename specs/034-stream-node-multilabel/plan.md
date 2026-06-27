# Implementation Plan: 스트림 node_event 멀티라벨

**Branch**: `refactor` | **Date**: 2026-06-27 | **Spec**: [spec.md](./spec.md)

## Summary
`emit_nodes`(graph_stream.py)가 node_event에 `labels` 배열(=[타입]+stereotype)을 추가 발신. `label`(단일=타입)은 back-compat 유지. step7 summarize_module이 적재 후 emit_nodes 호출(모듈 역할 라이브). 위치 일관(labels[0]=타입, spec 033/035).

## Technical Context
- Python async, NDJSON 스트림. 변경: `shared/workflow/graph_stream.py`(emit_nodes + `_node_labels` 헬퍼), `analysis_flow/step7_post_processing/summarize_module_step.py`(emit 추가).
- 검증: py_compile + 라이브 분석 스트림 캡처(node_event.labels에 stereotype 확인).

## Constitution Check
- III 단일진실: 라벨 산출 1곳(`_node_labels`). stereotype=속성/라벨 동일 출처(노드). ✅
- IV silent fail: 없음(순수 발신). ✅
- 회귀: `label` 유지 + stereotype 없으면 labels=[타입] = 기존 동작. ✅

## Project Structure
```
shared/workflow/graph_stream.py            # _node_labels 헬퍼 + emit_nodes에 labels 추가
analysis_flow/step7_post_processing/summarize_module_step.py  # upsert 후 emit_nodes
```

## Complexity Tracking
> 위반 없음.
