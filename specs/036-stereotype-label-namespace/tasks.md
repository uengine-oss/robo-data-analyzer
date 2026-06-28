# Tasks: stereotype 라벨 네임스페이스 분리 (UPPER_SNAKE)

**Spec**: [spec.md](./spec.md) · **Plan**: [plan.md](./plan.md)
**입력 설계**: 생산자 단일 함수(analyzer) + 소비자 2파일(frontend). 신규 스키마 0.

> 규칙: `[P]` = 다른 파일이라 병렬 가능. 각 태스크에 정확한 파일 경로. 구현 전 코드 0줄(SDD).

## Phase 1: 핵심 — US1 데이터 유실 제거 (P1) 🎯 MVP

- [ ] **T001** `robo-data-analyzer/shared/neo4j/nodes/stereotype_label.py`: camelCase→UPPER_SNAKE 변환 헬퍼 추가(`ApplicationService`→`APPLICATION_SERVICE`, `Command`→`COMMAND`, `DataMutator`→`DATA_MUTATOR`). 정규식 1줄(`re.sub(r'(?<!^)(?=[A-Z])','_',s).upper()`), 가독성 위해 이름 있는 함수.
- [ ] **T002** 동 파일 `apply_stereotype_labels`: 라벨 그룹핑·`_set_label_query` SET 직전 stereotype을 T001 헬퍼로 변환. 빈 stereotype 스킵 유지. (코드노드·모듈 단일 chokepoint → FILE/FUNCTION 라벨 일괄 적용.)
- [ ] **T003** 속성 불변 확인: `shared/neo4j/nodes/code_node.py:61`·`module.py:39`의 `"stereotype"` 속성은 PascalCase 그대로(수정 금지) — grep으로 라벨 SET 외 stereotype 라벨 사용처 0 재확인(§5 전수 grep).

**체크포인트(US1)**: 재분석 후 stereotype 라벨이 UPPER_SNAKE, architect `_WIPE_LABELS`(PascalCase)와 불일치 → 충돌·고아 0.

## Phase 2: US2 소비처 무중단 (P2)

- [ ] **T004 [P]** `robo-data-frontend/src/shared/utils/nodeIdentity.ts` `buildIdentityRows`(86–89): 역할 표시를 **속성 `p.stereotype`(PascalCase) 우선** 조회로 조정 → `STEREOTYPE_KO`(PascalCase 키) 무수정으로 한글 유지. 라벨(UPPER_SNAKE)은 색·필터용 그대로.
- [ ] **T005 [P]** `robo-data-frontend/src/shared/stores/navigator.ts:49–50,87`: stale 역할 필터 리스트를 현 `ontology_roles.yaml` 풀의 UPPER_SNAKE 값으로 정정(또는 위치기반으로 일반화해 드리프트 제거).
- [ ] **T006** architect 읽기 무영향 확인: `robo-architect/.../mapper/module_retriever.py`가 stereotype을 **속성**으로 읽음을 재확인(이미 확인됨) — 코드 변경 없음, 회귀 점검만.

## Phase 3: 검증 (구현 후, principles §6)

- [ ] **T007** 재분석: `cd robo-data-analyzer && python cli_scripts/analyze_data_dir.py zapamcom --strategy dbms`.
- [ ] **T008** Neo4j 점검: 고아 RULE=0 / 함수 노드=AST 정의수(38) / stereotype 라벨 전부 UPPER_SNAKE (SC-001).
- [ ] **T009** 충돌 회귀 테스트: analyzer 영속 DB에 `_WIPE_LABELS` 기반 `MATCH (n:Command…) DETACH DELETE` 모의 → analyzer 함수·HAS_RULE 삭제 0건 (SC-002).
- [ ] **T010** 프론트 자가 시각검증: 노드패널 "역할" 한글 표시 회귀 0 (SC-003) — 풀스택+Playwright 헤드리스([[manual]] §3).

## 교차 알람 (본 스펙 밖)

- [ ] **X1** robo-architect spec 044: ES 인제스천 삭제를 공유 라벨이 아닌 자기 표식 기반으로(이중 안전판). robo-spec 체계에서 별도 진행.

## 의존성

```
T001 → T002 → (T003 점검)            [analyzer, 순차]
T004 [P] · T005 [P]                   [frontend, 병렬 — 다른 파일]
T002·T004·T005 완료 → T007 재분석 → T008·T009·T010 검증
```

**MVP = Phase 1(T001–T003)**: 이것만으로 데이터 유실(US1) 해결. Phase 2는 표시 회귀 방지(US2).
