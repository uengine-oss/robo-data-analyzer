# Tasks: 스트림 node_event 멀티라벨

**Spec**: [spec.md](./spec.md) | **Plan**: [plan.md](./plan.md)

- [ ] T001 `shared/workflow/graph_stream.py` — `_node_labels(node)` 신규: `[_node_label(node)] + ([node.stereotype] if getattr(node,'stereotype',None) else [])`. `emit_nodes`가 node_event에 `"labels": _node_labels(node)` 추가(기존 `"label"` 유지=labels[0]).
- [ ] T002 `analysis_flow/step7_post_processing/summarize_module_step.py` — `upsert_modules(neo, updated)` 후 `emit_nodes(stream, updated)` 호출(모듈 stereotype 라벨 라이브 발신). import 추가.
- [ ] T003 py_compile 2파일.
- [ ] T004 라이브 분석 스트림 캡처 → 함수 node_event `labels`=[FUNCTION,<role>], 모듈=[FILE,Service] 확인(SC-001/002).

## 의존
T001 → T002 → T003 → T004.
