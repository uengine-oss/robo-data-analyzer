# Tasks: framework 내부 모듈 호출 정규 해소

**Spec**: [spec.md](./spec.md) | **Plan**: [plan.md](./plan.md)

변경 = `shared/lang/call_classifier.py` 1파일. 신규 노드/라벨/계약 0.

- [ ] **T001** 래퍼 경로(현 L190) `_wrapper_callee_to_internal(extracted)` → `_resolve_internal(extracted, caller, by_name, module_of)` 로 대체. 결과 None이면 기존대로 external 유지. [FR-001/002/003/006]
- [ ] **T002** 데드 제거: `entry_by_module`/`other_by_module` 구축 블록(L134-142)·`_wrapper_callee_to_internal` 정의(L144-151) 삭제. [FR-007, 원칙 III·VIII]
- [ ] **T003** 전수 grep: `_wrapper_callee_to_internal`·`entry_by_module`·`other_by_module` 잔존 참조 0 확인(지역정의라 외부 0 예상). `py_compile` 통과. [원칙 §5]
- [ ] **T004** 라이브 검증(framework 재분석, neo4j DB): ① 이름 'zapamcom10060' EXTERNAL 스텁 0(SC-001) ② zapamcom10140→zapamcom10060 internal Calls 존재(SC-002) ③ zngmmcap50000 등 진짜 외부 external 유지(SC-003) ④ internal/external 집계 무회귀(SC-004).
- [ ] **T005** 커밋(analyzer refactor, 내 파일만: call_classifier.py + specs/030/). 신원 ahnchiyoon87, fetch→rebase→push.

## 의존
T001→T002→T003→T004→T005. T001/T002 같은 함수 내 인접 → 함께 편집.
