# Tasks: 노드 id 전역 유일성 + 반복 관측 정규화

**Spec**: [spec.md](./spec.md) · **Plan**: [plan.md](./plan.md)

- [x] T001 [US4] `shared/test_node_registry.py`에 동일 실체 정규화·타입 충돌·값 충돌 실패 테스트를 먼저 추가하고 실패 확인.
- [x] T002 [US4] `shared/node_registry.py`에 순서 보존형 공통 등록기와 명시적 `NodeIdentityCollision` 구현.
- [x] T003 [US4] `analysis_flow/step2_load_ast/load_ast_step.py`의 여섯 노드 리스트를 등록기로 정규화한 뒤 기존 유일성 가드 실행.
- [x] T004 [US4] 정규화 건수를 `load_ast_step` 구조화 로그에 남기고 실제 충돌은 `log_error` 후 재전파.
- [x] T005 [US1] `shared/workflow/executor.py` 최종 run 실패 NDJSON에 `fatal: true`를 명시해 소비자가 성공과 구분하도록 계약 보강.
- [x] T006 [US4] 단위 테스트 및 Python compile/import 검증.
- [x] T007 [US1] `shopmall` 실제 AST 12개로 Package 1개·Module 12개·`load_ast_step` 완료 검증.
- [ ] T008 전체 분석 후 중복 id 0과 TABLE 오염 0을 Neo4j에서 검증.
- [x] T009 [US3] 삭제된 id 파싱 helper의 잔존 import를 제거하고 scan overlap guard를 `CodeNode.name` 계약으로 전환. 가드 3건·호출분류 3건·`main` import·임시 포트 `/` readiness 검증.
