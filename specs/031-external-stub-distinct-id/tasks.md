# Tasks: 외부 스텁 모듈·엔트리 함수 id 구분

**Spec**: [spec.md](./spec.md) | **Plan**: [plan.md](./plan.md)

변경 = `shared/neo4j/edges/calls.py` `upsert_calls` 1곳. 신규 라벨/관계/계약 0.

- [ ] **T001** `upsert_calls` external dict: `module_id = e.callee_module or e.callee_id`, `name = e.callee_name or e.callee_id`, `func_id = f"{module_id}.{name}"` → dict `callee_id=func_id`, `callee_module=module_id`, `callee_name=name`. [FR-001/002/004]
- [ ] **T002** `EXTERNAL_QUERY` 무변경 확인(함수 id≠모듈 id 로 별개 노드 생성·HAS_MEMBER 유지). [FR-003]
- [ ] **T003** py_compile + 외부 callee_id 소비처(build_context/three_split) 무회귀 확인(외부=라벨 처리). [FR-005/006]
- [ ] **T004** 라이브 검증(framework 재분석): 외부 이름별 MODULE id ≠ FUNCTION id(같은-id 0), 각 모듈 HAS_MEMBER 함수 1+, 프론트 네비 외부 모듈 아래 함수 표시(childN≥1). 내부 무회귀. [SC-001~003]
- [ ] **T005** 커밋(analyzer refactor, 내 파일만: calls.py + specs/031). + 잘못된 스펙 정정(027 외부서술·todo).

## 의존
T001→T002→T003→T004→T005.
