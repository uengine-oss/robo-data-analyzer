---
description: "Task list — 그래프 스키마 정합 (DBMS 흐름·구조 복원 + 관계 범용 통합)"
---

# Tasks: 그래프 스키마 정합 — DBMS 흐름·구조 복원 + 관계 범용 통합

**Input**: `specs/018-graph-schema-realign/` (plan·spec·research·data-model·contracts·quickstart)

**Tests**: 단위테스트 없음 — 검증은 실제 파이프라인 실행 후 그래프 원문 대조(quickstart V1~V7, constitution Cost&Safety).

**경로**: 생산자 = `robo-data-analyzer/`. 소비처 = `robo-data-frontend/`, `robo-data-catalog/`, `robo-architect/`.

## Format: `[ID] [P?] [Story] Description`

---

## Phase 1: Setup

- [ ] T001 옛 main `analyzer/strategy/dbms/{analysis/ast_collector.py, common/cypher/graph_queries.py}` 의 NEXT(prev→curr)·StatementCollector(NON_ANALYSIS) 로직을 라인 단위로 추출해 본 작업 참조 노트로 정리(research.md A 보강).

---

## Phase 2: Foundational (Blocking — 관계 단일 진실 먼저)

**⚠️ 모든 스토리가 이 스키마에 의존. 먼저 완료.**

- [ ] T002 `shared/neo4j/rel_types.py` 관계 상수 정리(단일 진실): `BELONGS_TO` 끝점 주석을 "네임스페이스 소속(그릇→폴더/패키지, 테이블→스키마)"로 확장, `HAS_MEMBER` 주석을 "그릇→루틴/데이터 멤버"로 확장(기존 DataType→Variable 포함), `NEXT` 주석을 "구문 실행순서(DBMS) + Rule 순서(EM)"로, 옛 `HAS_FUNCTION`/`HAS_VARIABLE`/`BELONGS_TO_PACKAGE` 상수 **제거**.
- [ ] T003 [P] `shared/neo4j/edges/has_member.py` **확장**(신설 아님 — 이미 존재): MATCH 를 `(d:DATA_TYPE)` 한정에서 **무라벨 `(parent {id})`** 로 일반화(그릇→루틴/데이터 공통). 옛 `has_function.py`·`has_variable.py` **삭제**(흡수). `__init__.py` export 정합.
- [ ] T004 [P] `shared/neo4j/edges/belongs_to.py` 단일화 — 기존(Table→Schema)에 그릇→Package 포함하도록 MATCH 일반화. 옛 `belongs_to_package.py` **삭제**(흡수).
- [ ] T005 [P] `shared/neo4j/edges/next.py` **신규**: 구문→구문 `upsert_statement_next`(무라벨 `MATCH (prev {id}) MATCH (curr {id}) MERGE (prev)-[:NEXT]->(curr)`). ★기존 `edges/rule_flow.py`(NEXT=RULE→RULE, owner+local_rule_id 매칭)는 **불변 유지** — 별개 파일·다른 MATCH, rel 타입 문자열만 공유.
- [ ] T006 `data_models/graph_edges.py` dataclass 정합: 옛 `HasFunction`/`HasVariable`/`BelongsToPackage` **삭제**(→ 기존 `HasMember`·`BelongsTo` 로 대체), 구문 흐름용 **신규 `StatementNext(prev_id, next_id)` 추가**. ★기존 `Next`(owner_id/from_rule_id/to_rule_id, Rule-flow)·`BelongsTo`(Table→Schema)·`HasMember`(DataType→Variable)는 유지·확장. `load_ast_step.py` 의 import·extend·upsert 호출·반환 dict 키 정합.
- [ ] T006b 🆕 `shared/workflow/state.py` `WorkflowState` 정합: `has_function_edges`/`has_variable_edges`/`belongs_to_package_edges` 키 → `has_member_edges`(기존)·`belongs_to_edges`(기존, 주의: step1 Table→Schema 와 키 공유 가능성 검토) 로 통합 + import 정합. 구문 흐름용 **신규 키 `statement_next_edges: list[StatementNext]`** 추가(기존 `next_edges`=Rule-flow 재사용 금지=덮어쓰기 회귀).
- [ ] T006c 🆕 `shared/workflow/artifact_writer.py` `_CATEGORY` 맵 정합: 삭제·rename 된 state 키 제거, 신규 `statement_next_edges`→"ast"·통합 키 정합(미정합 시 graph_extras.json + warning = 품질저하).
- [ ] T007 [P] 구조노드 라벨 단일 맵 `STRUCTURAL_LABELS={DECLARE:"변수 선언부",TRY:"예외 처리",TRIGGER_BLOCK:"트리거 본문"}` 를 단일 정의처(`shared/lang/` 상수)에 추가. (생산자는 라벨만 antlr type 그대로 — 한글 표시는 frontend 소비, T014.)
- [ ] T008 ★심볼변경 즉시 전수 grep: `HAS_FUNCTION`/`HAS_VARIABLE`/`BELONGS_TO_PACKAGE`(상수)·`HasFunction`/`HasVariable`/`BelongsToPackage`(dataclass)·`has_function_edges`/`has_variable_edges`/`belongs_to_package_edges`(state 키)·`upsert_has_function`/`upsert_has_variable`/`upsert_belongs_to_package`(함수)·`has_function.py`/`has_variable.py`/`belongs_to_package.py`(파일) 가 analyzer 내 참조 0 인지 확인. ★`calls.py` external stub(`:MODULE:EXTERNAL`-[:HAS_FUNCTION]->·[:BELONGS_TO_PACKAGE])도 정합 대상.

**Checkpoint**: 관계/노드 스키마 단일 진실 확정 + state/artifact 정합 + 옛 심볼(상수·dataclass·state키·함수·파일) 잔재 0. reader·소비처 작업 가능.

---

## Phase 3: User Story 1 — DBMS 구문 실행 흐름 (NEXT) (P1) 🎯 MVP

**Goal**: 프로시저 내 형제 구문이 실행 순서로 NEXT 연결.
**Independent Test**: quickstart V1 — `(a)-[:NEXT]->(b)` 라인순 연결, framework 0.

- [ ] T009 [US1] `analysis_flow/step2_load_ast/antlr_json_reader.py::_walk_dbms` — 같은 부모 아래 형제 **구문(Function 노드)** 을 등장순으로 `StatementNext(prev_id, curr_id)` 생성, `ParseResult.statement_next_edges` 에 append(변수/상수/구조 비순서 노드 제외 여부는 V1 대조로 결정). framework `_walk_framework`·`_read_framework` 는 생성 안 함.
- [ ] T010 [US1] `antlr_json_reader.ParseResult` 에 `statement_next_edges` 필드 추가 + `load_ast_step.py` 가 extend·`upsert_statement_next` 호출·`{"statement_next_edges": ...}` 반환(T005·T006·T006b edge/state 사용). ★기존 `next_edges`(Rule-flow) 경로 불변.
- [ ] T011 [US1] 검증: RWIS 재분석 → V1 쿼리로 NEXT 흐름 확인(원문 라인순 대조).

**Checkpoint**: 구문 흐름 복원.

---

## Phase 4: User Story 2 — 구조 노드(DECLARE) 복원 + 표시 (P1)

**Goal**: DECLARE 등 구조 노드를 그래프에 보존(LLM만 스킵), "구조" 표시.
**Independent Test**: quickstart V2 — DECLARE 노드 존재, summary 비어도 정상, "변수 선언부(구조)".

- [ ] T012 [US2] `antlr_json_reader.py::_walk_dbms` — DECLARE 를 통째 스킵하지 말고 **노드로 생성**(다른 구문처럼, primary_label=DECLARE). 자식은 그 아래 PARENT_OF.
- [ ] T013 [US2] `step6_analyze_function/batch_planner.py::_SKIP_ANALYSIS_TYPES` 가 DECLARE 를 LLM 분석만 제외(그래프 유지)하도록 확인/정합 — reader 가 노드 생성하니 skip 경로가 실제 작동.
- [ ] T014 [US2] 소비처 표시: frontend 가 구조 라벨 맵(T007 대응) 사용 — `nodeClassify`/`graphDisplay` 에서 구조노드 = 한글 라벨 + "구조" 태그(논리명 모드 누락 오인 방지).
- [ ] T015 [US2] 검증: V2 — DECLARE 노드 존재 + 표시 확인(원문 DECLARE 블록 대조).

**Checkpoint**: 누락 0, 구조/누락 구분.

---

## Phase 5: User Story 3 — HAS_MEMBER 단일 조회 (관계 통합) (P2)

**Goal**: 그릇 멤버를 단일 관계로 조회, 종류=라벨. 옛 관계명 잔재 0.
**Independent Test**: quickstart V3·V4 — HAS_MEMBER 하나로 멤버 조회, 옛 이름 0.

- [ ] T016 [US3] `antlr_json_reader.py::_derive_structure_edges` — HAS_FUNCTION/HAS_VARIABLE 생성 → `HasMember` 단일 생성. BELONGS_TO_PACKAGE → `BelongsTo`.
- [ ] T017 [P] [US3] frontend `shared/utils/nodeRelationships.ts` (getRelationshipGroups·getAttachedByType) — HAS_FUNCTION/HAS_VARIABLE → HAS_MEMBER + 라벨 필터.
- [ ] T018 [P] [US3] frontend `shared/stores/navigator.ts`(moduleTree) — HAS_MEMBER + 라벨로 멤버 구성.
- [ ] T019 [P] [US3] frontend `node-detail/{ModuleCard,PackageCard,FileCard,DatatypeCard}.vue` — HAS_MEMBER + 라벨 필터(루틴/데이터).
- [ ] T020 [P] [US3] frontend 기타 소비처 정합: `graphDisplay.ts`(friendlyEdgeLabel 맵)·`graphStyles.ts`·`GraphTab.vue`·`graphCanvas.ts`·`TableCard.vue`·`MultiLayerOntologyViewer.vue`.
- [ ] T021 [P] [US3] catalog `robo-data-catalog/service/schema_query_service.py:169` — `(m:MODULE)-[:HAS_FUNCTION|HAS_VARIABLE]->(src)` → `(m)-[:HAS_MEMBER]->(src)`(MODULE 라벨 제약 제거 = relabel 안전). 위치: `robo-architect/robo-analyzer/robo-data-catalog`(catalog 는 architect 서브모듈에만 존재, 루트 별도체크아웃 없음).
- [ ] T022 [P] [US3] architect `api/features/ingestion/hybrid/mapper/rule_context.py:27-28`·`agent_validator.py:132-133` — `(mod)-[:HAS_FUNCTION]->(f)`→`[:HAS_MEMBER]`, `(mod)-[:BELONGS_TO_PACKAGE]->(pkg:PACKAGE)`→`[:BELONGS_TO]`(mod 무라벨=relabel 안전, pkg:PACKAGE 유지). ★architect 는 별개 git(공유 main) — 커밋·서브모듈 bump 순서 주의([[manual]] §7). 본 2파일은 [[todo]]① id정합과 다른 작업(관계명).
- [ ] T023 [US3] ★전수 grep 재확인: 4영역 전체에서 `HAS_FUNCTION`/`HAS_VARIABLE`/`BELONGS_TO_PACKAGE` **0건**(옛 이름 잔재 0).
- [ ] T024 [US3] 검증: V3·V4.

**Checkpoint**: 관계 통합 + 소비처 정합 + 회귀 점검.

---

## Phase 6: User Story 4 — HAS_MEMBER 루틴 한정 ✅ / 그릇 라벨 → 019 이관

> **★분할(2026-06-19)**: (a) HAS_MEMBER 루틴 한정 = ✅ US3 producer 구현에 포함(완료). (b) 그릇 라벨 정체화(MODULE→FILE/CLASS) = **spec 019 로 이관**(최종모델: 타입 단일·역할 폐기). T025·T027·T028 은 **019 권위**(여기선 폐기).

- [x] T026 [US4] `_derive_structure_edges` — HAS_MEMBER 를 **루틴(PROCEDURE/FUNCTION/METHOD/TRIGGER)+데이터에만**, 일반 구문 제외(PARENT_OF+NEXT). ✅ US3 reader 구현에 포함.
- [→019] ~~T025 컨테이너 라벨 MODULE→FILE/CLASS~~ → spec 019(타입 단일·역할 폐기, 구현됨).
- [→019] ~~T027 frontend FILE/CLASS 라우팅~~ → 019(templateRouter, 구현됨).
- [→019] ~~T028 검증 V5~~ → 019 quickstart.

**Checkpoint**: 라벨 정체화, "함수 N" 정상화.

---

## Phase 7: User Story 5 — 룰 중복·열화 방지 (P3)

**Goal**: 룰=프로시저 단위, 통째 우선/계층 시 정확룰 상승+추상화+dedup.
**Independent Test**: quickstart V6 — 의미중복 0, 세부 보존.

- [ ] T029 [US5] `step6_analyze_function/batch_analyze.py`·`range_body.py` — 단위가 한 콜에 들어가면 통째 1콜(분할 안 함). 임계 초과 시만 계층.
- [ ] T030 [US5] 계층 시 자식의 **정확한 룰**(요약 아님)을 부모 컨텍스트로 전달(`_build_batch_values` 에 자식 룰 주입) + batch 프롬프트에 "자식룰 복창금지·상위추상화" 지침(★KV캐시 byte-identical 유지).
- [ ] T031 [US5] 룰 단일 소유(노드별 1회 저장) + 최종 의미 dedup(EM 누적 단계).
- [ ] T032 [US5] 검증: V6 — 중복·열화 원문 대조.

**Checkpoint**: 룰 품질.

---

## Phase 8: Polish & 검증 (Cross-Cutting)

- [ ] T033 architect 내 analyzer 사본(서브모듈) 동기화 — 생산자 변경 후 포인터 bump([[manual]] §7).
- [ ] T034 spec009/016/017 등 관련 스펙 문서의 관계명·구조노드 서술 정합(doc 드리프트 0).
- [ ] T035 ★프론트 UI 자가검증(Playwright 스크린샷) — 그래프·네비·노드패널 회귀 0([[manual]] §3).
- [ ] T036 `/speckit-analyze` 품질게이트(spec↔plan↔tasks 교차검증).
- [ ] T037 quickstart V1~V7 전체 통과 — RWIS 재분석 후 원문 대조(누락/왜곡/중복/열화 0). ★분석 실행은 사용자 OK 후.
- [ ] T038 커밋(신원 ahnchiyoon87) — analyzer refactor / frontend main / catalog / architect main(공유 fetch 먼저). 사용자 OK 후.

---

## Dependencies & Execution Order

- **Phase 2(Foundational)** = 모든 스토리 선행. (rel_types 단일 진실 먼저.)
- US1(NEXT)·US2(DECLARE) = P1, Foundational 후 독립. US1 이 MVP(흐름 복원).
- US3(HAS_MEMBER 통합) = 소비처 정합 대량(T017~T022 [P] 병렬). US4 의 라벨 한정과 함께 검증.
- US4 = US3 의 관계 통합 후(HAS_MEMBER 존재 전제).
- US5(룰) = 독립(분석 단계), 마지막.
- Phase 8 = 전부 후. T037(검증)·T038(커밋)은 **사용자 OK 후**.

### Parallel Opportunities
- Foundational: T003·T004·T005·T007 [P].
- US3 소비처: T017~T022 [P](서로 다른 파일·레포).

## Implementation Strategy
- **MVP** = Phase2 + US1(NEXT) → 흐름 복원 검증.
- 증분: +US2(DECLARE) → +US3(통합)+US4(라벨) → +US5(룰) → 검증·커밋.
- ★구현 중 심볼 변경마다 즉시 전수 grep(§5). 분석/커밋은 사용자 OK 게이트.

## Notes
- [P]=다른 파일·무의존. 회귀 0(소비처 옛 이름 잔재 0).
- 검증=실제 파이프라인+원문 대조. 분석 실행·커밋=사용자 OK 후.
