# Tasks: 역할(stereotype) 노드 라벨 재부여 + 위치기반 소비 계약

**Spec**: [spec.md](./spec.md) | **Plan**: [plan.md](./plan.md)

생산자측(analyzer)만. 소비처(frontend) 위치기반 이관은 후속 spec. 코드는 철회된 `d61f240` 복원.

## Phase 1 — 라벨 부여 헬퍼 복원

- [ ] T001 `shared/neo4j/nodes/stereotype_label.py` 신규 — `apply_stereotype_labels(client, pairs)`: `(id, stereotype)` 쌍을 stereotype별로 묶어 `UNWIND … MATCH (node {id}) SET node:`<값>``. 빈 stereotype 건너뜀. 값별 배치(동적 라벨). 백틱 방어. (d61f240 동일)

## Phase 2 — 호출부 배선

- [ ] T002 `shared/neo4j/nodes/code_node.py` `update_summaries`: `apply_stereotype_labels` import + 분석된 노드(`summary is not None`)에 `apply_stereotype_labels([(f.id, f.stereotype or "") for f in analyzed])` 호출(속성 SET 유지=무중단). step6 적재 시점이라 타입 라벨(step2) 이후.
- [ ] T003 `shared/neo4j/nodes/module.py` `upsert_modules`: import + 라벨 MERGE 루프 **이후** `apply_stereotype_labels([(m.id, m.stereotype or "") for m in modules])` 호출. step2 호출 시 stereotype 빈값=no-op, step7 호출 시 부여.

## Phase 3 — 검증

- [ ] T004 `py_compile` 3파일(stereotype_label·code_node·module).
- [ ] T005 현재 그래프 마이그레이션 또는 framework 재분석 → Neo4j `labels(n)` 쿼리로 SC-001~004 확인(보유 노드 100% stereotype 라벨, `labels[0]`=타입, 속성·id·관계 불변). [quickstart.md](./quickstart.md) 절차.

## 의존

T001 → T002·T003(병렬 가능, 다른 파일) → T004 → T005.
