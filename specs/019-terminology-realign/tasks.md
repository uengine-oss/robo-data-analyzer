---
description: "Task list — 용어 정체화 (그릇 라벨 + 로그 종류 + 분석단위 용어 CodeNode)"
---

# Tasks: 용어 정체화 — framework 중심 노드/타입/로그 용어 제거

**Input**: `specs/019-terminology-realign/` (plan·spec·research·data-model·contracts·quickstart)

**Tests**: 단위테스트 없음 — 검증 = 실제 파이프라인 실행 후 그래프·로그 원문/실제종류 대조(quickstart V1~V6).

**경로**: 생산자 = `robo-data-analyzer/`. 소비처 = `robo-data-frontend/`, `robo-data-catalog/`, `robo-architect/`.

> **★★최종 확정 모델 (2026-06-19, 사용자 확정) — 예외 0**: 모든 노드 라벨 = **자기 antlr `type` 하나**. 역할 라벨(FUNCTION/MODULE/VARIABLE/CONSTANT/DATA_TYPE) **전면 폐기**. 생산자에서 `primary_label`/`sub_label` 칸 + ontology `sub_label_of`/`primary_label_of`/`_ROLE_TO_LABEL` **삭제**. 라벨 = `node["type"]` 직결. ontology `role_of`(분류=어느 dataclass/엣지/internal)는 **유지**(언어별 module 기준 출처 — 라벨엔 안 씀). 역할(호출가능/그릇/변수…)은 소비처가 **타입집합으로 파생**(상세=data-model §1).
> - ✅ **그릇 라벨(US1) 구현됨**: `nodes/module.py`(:FILE/:CLASS 단일)·frontend `isContainerNode`/navigator/templateRouter/graphStyles. → 확정모델로 `m.sub_label`→`m.label`(=node type) 정리만 남음.
> - ✅ **US2(로그) 부분구현**: `analyze_function_step`=`fn.primary_label` → 확정모델로 `fn.label`(실제종류) 정리. ★B1: framework 메서드는 primary_label="FUNCTION"이라 로그가 METHOD 아닌 FUNCTION으로 떴음(표시-영속 불일치) → label로 해소.
> - ⬜ **잔여 = 라벨=antlr type 전면화**(함수+변수+상수+데이터타입): reader+graph_nodes(label 단일화)·ontology(변환기 삭제)·nodes/*.py·schema_constraints(id기반)·embed_step(B2: `MATCH(:FUNCTION)`→무라벨 id) + **references/init_by/build_context**(VARIABLE/CONSTANT 라벨→id무라벨·멤버십, LLM 입력 byte 동일) + 소비처 frontend 타입집합·catalog·architect(엣지/타입집합, bump는 OK후) + US3(Function→CodeNode 개명) + US5(룰) + 재분석·검증·커밋.
> - ★검증완료(전수 grep): 딥에이전트·LLM분석·전략선택·링킹(call_linker=parent_id/own_ast_type)·컨텍스트(callee=id+요약)는 **라벨 비의존** → 라벨 변경 영향 0. 루틴 식별=`role_of`+`_ROUTINE_LABELS`(METHOD 포함).

## Format: `[ID] [P?] [Story] Description`

---

## Phase 1: Setup

- [ ] T001 research.md A(blast radius)·D6(단계화) 기준 작업 노트 확정. ★구현 중 심볼 개명마다 즉시 전수 grep(원칙 §5). 분석 실행·커밋 = 사용자 OK 게이트.

---

## Phase 2: Foundational (Blocking — 종류 도출·표시 단일점 먼저)

**⚠️ US1·US2 가 공유. 먼저 완료.**

- [ ] T002 종류 출처 확인(단일 진실): `shared/lang/ontology.py`·`ontology_roles.yaml` 가 그릇 종류(java→CLASS, c/py→FILE)를 `sub_label_of` 로 도출함을 확정 — 신규 매핑 없이 재사용. DBMS 빈값 → FILE 기본 규칙 정의 위치 결정.
- [ ] T003 **노드 → 표시 종류** 단일 헬퍼 결정/신설(생산자): 한 노드의 "실제 종류"(primary_label 또는 라벨)를 돌려주는 단일 함수 — 로그(US2)·라벨(US1)이 공유(DRY, 표시-영속 일치 보장). 위치 = `shared/`(예: nodes 공통 또는 lang).
- [ ] T004 [P] frontend 종류 표시 설계 확정: `nodeClassify.ts` `ROLE_LABELS` 에서 **`FILE` 제외**(FILE=실제 종류로 표시). 그릇 판정 = **타입 집합 `isContainerNode`** {FILE,CLASS,INTERFACE,RECORD}(enum=DATA_TYPE 제외) — 역할 라벨(MODULE) 아님. 단일 정의처화. (MODULE 은 외부 stub 호환으로만 셋에 잔류.)

**Checkpoint**: 종류 출처·표시 단일점 확정. US1/US2 작업 가능.

---

## Phase 3: User Story 2 — 로그/스트림이 실제 종류 (P1) 🎯 권장 선착수(저위험·독립)

**Goal**: 진행 로그·스트림 노드 종류 = 실제 종류(영속 라벨 일치). 고정 "FUNCTION" 제거.
**Independent Test**: quickstart V3 — node_event 종류가 SELECT/IF/PROCEDURE/FILE, "FUNCTION" 고정 0, 영속 라벨과 일치.

- [x] T005 [US2] `analyze_function_step.py::_emit_function_graph` — node_event(377) + rule-flow from/to(407~433) `"label":"FUNCTION"` 고정 → `node_label = fn.primary_label or "FUNCTION"`(실제 종류) 단일변수. ✅ 2026-06-19 구현·py_compile OK·하드코딩 잔존 0.
- [ ] T006 [US3] (재분류) `step7_post_processing/embed_step.py:68` `("FUNCTION", function_updates)` 는 **로그 아니라 임베딩 MATCH 라벨**(`MATCH (n:FUNCTION {id})`) — DBMS 노드(:SELECT 등) 임베딩 누락되는 별개 이슈. US3/라벨 작업에서 **무라벨 id MATCH**로 전환(state 키 code_nodes 개명과 함께).
- [ ] T007 [US2] 검증: V3 — RWIS 재분석 로그/스트림에서 종류 실제값 + 영속 대조(★분석 사용자 OK 후).

**Checkpoint**: 로그 정체대로(사용자 "로그도 function" 불만 해소).

---

## Phase 4: User Story 1 — 그릇 노드 정체대로 (MODULE→FILE/CLASS) (P1)

**Goal**: 그릇 = 실제 종류 단일 라벨(MODULE 폐기), DBMS=FILE. 소비처는 타입집합으로 그릇 식별. navigator 회귀 0.
**Independent Test**: quickstart V1·V2.
**⚠️ 생산자+프론트 원자 정합 — 한쪽만 하면 navigator MODULES 빈 회귀(반응형 금지).**

- [ ] T008 [US1] `shared/neo4j/nodes/module.py` 라벨 = 실제 종류 **단일**(`m.sub_label or "FILE"`) — `:FILE`/`:CLASS`, MODULE 폐기. docstring 정합. (✅ 구현됨.)
- [ ] T009 [US1] `analysis_flow/step2_load_ast/antlr_json_reader.py` — DBMS 그릇이 종류 FILE 을 갖도록(reader 가 sub=FILE 부여 또는 module.py 기본값과 합치). `_make_module` 정합.
- [ ] T010 [US1] `shared/neo4j/schema_constraints.py:29` `uniq_module(:MODULE)` → 실제 종류 라벨(FILE/CLASS) 유니크 제약 추가(+ external :MODULE 용 기존 유지 검토). MERGE-by-id 폴백 문서화.
- [ ] T011 [P] [US1] frontend `shared/utils/nodeClassify.ts` — ROLE_LABELS 에서 FILE 제외(T004), classifyNode 컨테이너 판정에 CLASS 등 포함.
- [ ] T012 [P] [US1] frontend `shared/stores/navigator.ts` — 그릇 매칭을 **타입집합 `isContainerNode`**(buildPkgTree·flat)로, 종류 표시는 실제 라벨. 트리 빈칸 0 확인. (✅ 구현됨.)
- [ ] T013 [P] [US1] frontend `node-detail/templateRouter.ts` — FILE→file 카드(이미)·CLASS/INTERFACE/RECORD→module 카드 라우팅(라벨 순서 무관).
- [ ] T014 [P] [US1] frontend `shared/config/graphStyles.ts`(색·크기·PRIMARY_LABELS)·`features/code-analysis/analyzer.ts`(PRIMARY_LABELS)·`graphCanvas.ts`(module-interaction hasLabel) — FILE/CLASS 포함.
- [ ] T015 [US1] 검증: V1·V2 — 그릇 종류 표시 + navigator/카드 회귀 0(★프론트 UI 내가 Playwright 자가검증, [[manual]] §3).

**Checkpoint**: 그릇 정체대로, MODULE 단독 표기 0(외부 stub 제외).

---

## Phase 5: User Story 3 — 분석단위 용어 CodeNode (P2)

**Goal**: `Function`/`function_*` → 종류중립 CodeNode. 실제 루틴 라벨·판별은 유지.
**Independent Test**: quickstart V4 — 분석단위 지칭 function 잔재 0, 소비처 회귀 0.

- [ ] T016 [US3] `data_models/graph_nodes.py` `Function` dataclass → `CodeNode`(필드 불변). 전 import 정합(★심볼변경 즉시 전수 grep).
- [ ] T017 [US3] `shared/workflow/state.py` state 키 `functions`→`code_nodes`(+ Annotated/주석) · `artifact_writer.py` _CATEGORY · `logger.py` 키 맵 정합.
- [ ] T018 [US3] `shared/neo4j/nodes/function.py` `upsert_functions`/`update_summaries`→`upsert_code_nodes`/… (파일명 유지/변경 결정). 라벨 작성은 실제 종류(primary_label) — :FUNCTION/:PROCEDURE 등 실제 루틴 라벨 불변.
- [ ] T019 [US3] `analysis_flow/step6_analyze_function/` step 명·내부 `function_*` 식별자 → 종류중립(analyze_node 등). reader/링킹/EM 빌더의 분석단위 지칭 정합.
- [ ] T020 [US3] ★전수 grep: 분석단위 지칭 `Function`/`functions`/`analyze_function`/`upsert_functions`/`function_*` 잔재 0 (실제 루틴 의미 `:FUNCTION`/`:PROCEDURE`·`_ROUTINE_LABELS`·ontology 루틴 role 은 유지=정상, 오검출 구분).
- [ ] T021 [US3] 검증: V4 — 코드 grep + 소비처 동작 회귀 0.

**Checkpoint**: 분석단위 용어 중립화.

---

## Phase 6: Polish & 검증 (Cross-Cutting)

- [ ] T022 [P] 소비처 catalog `robo-data-catalog/service/schema_query_service.py` — `:MODULE` 등 라벨 참조 정합(실제 종류/역할 기준).
- [ ] T023 [P] 소비처 architect `api/features/ingestion/**` — 라벨 참조 정합. architect 내 analyzer 사본 서브모듈 bump([[manual]] §7).
- [ ] T024 ★KV 캐시: `prompts/**` system 본문에 "function/함수"가 분석단위 지칭으로 있으면 byte-identical 유지(개명이 프롬프트 안 바꿈) — `check_byte_identical.py`/`diff -q` 0 (V5).
- [ ] T025 doc 드리프트: README·관련 spec(004/016/017/018) 라벨·용어 서술 정합.
- [ ] T026 `/speckit-analyze` 품질게이트(spec↔plan↔tasks 교차검증).
- [ ] T027 quickstart V1~V6 전체 통과 — RWIS(+framework 샘플) 재분석 후 그래프·로그 원문/실제종류 대조(회귀·누수 0). ★분석 실행 = 사용자 OK 후.
- [ ] T028 커밋(신원 ahnchiyoon87) — analyzer refactor / frontend main / catalog / architect main(공유 fetch 먼저) + 서브모듈 bump. 사용자 OK 후. requirements.txt·module_test 제외.

---

## Dependencies & Execution Order

- **Phase 2(Foundational)** = US1·US2 선행(종류 출처·표시 단일점).
- **권장 구현 순서(research D6)**: US2(로그, 저위험·독립·즉효) → US1(그릇 라벨, 생산자+프론트 원자) → US3(코드 개명, 대규모 기계적).
- US1 = 원자(T008~T014 동시). US3 = 독립(개명), 회귀검증 위주.
- Phase 6 = 전부 후. T027(검증)·T028(커밋) = **사용자 OK 후**.

### Parallel Opportunities
- Foundational: T004 [P].
- US1 프론트: T011~T014 [P](서로 다른 파일).
- US2: T006 [P]. Polish: T022·T023 [P].

## Implementation Strategy
- **MVP** = Phase2 + US2(로그) → 사용자 로그 불만 즉시 해소.
- 증분: +US1(그릇 라벨, 원자) → +US3(CodeNode 개명) → polish·검증·커밋.
- ★심볼 개명마다 즉시 전수 grep(§5). 라벨은 생산자+소비처 원자(반응형 금지). 분석/커밋 = 사용자 OK 게이트.

## Notes
- [P]=다른 파일·무의존. 회귀 0(소비처 옛 가정 "그릇=MODULE"·"분석단위=function" 잔재 0).
- 검증=실제 파이프라인+원문/실제종류 대조. 018(US1-3)·017 결과 불변.
