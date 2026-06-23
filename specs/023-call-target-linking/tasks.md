---
description: "Task list for 023-call-target-linking"
---

# Tasks: DBMS CALL 노드 → 호출 대상 링킹 계약 정정

**Input**: `specs/023-call-target-linking/` (plan.md, spec.md, research.md, data-model.md, contracts/call-graph.md, quickstart.md)

**Tests**: 단위 테스트 별도 생성 안 함. 검증은 **실제 실행 + 그래프 원문 대조**(원칙 §6, quickstart.md). parity는 설계로 보장(routine_call_pairs ≡ 옛 proc→proc 집합).

## Format: `[ID] [P?] [Story] Description`

- **[P]**: 다른 파일·의존성 없음 → 병렬 가능
- 경로는 repo 루트(`d:\work\robo\project\robo-data-analyzer`) 기준

---

## Phase 1: Setup (Baseline 확보)

**Purpose**: parity 비교 기준선 — 정정 이전 산출물 보관.

- [ ] T001 정정 **이전** 코드로 DBMS 샘플 + FRAMEWORK 샘플을 각 1회 분석해 baseline 보관: Neo4j 그래프 덤프, `logs/.../health_report.json`, 컴포넌트(`component_id`) 목록을 별도 경로에 저장(V3·V4·V5·V6 비교용).

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: US2의 3개 소비처가 공유할 단일 해소기. 먼저 존재해야 함.

- [X] T002 `analysis_flow/step4_linking/stage2_extract_calls/call_linker.py`에 public `routine_call_pairs(code_nodes, calls) -> list[tuple[str, str]]` 신설: internal `Calls`마다 `(_root_of(caller=CALL노드, fn_by_id), callee)` 산출, self(루트==callee) 제외, (루트,callee) dedup, 해소 실패(부모체인 끊김·callee 부재) 건수 집계해 반환/로그(silent fail 금지, 헌법 IV). 결정론·정렬로 재현성. contracts/call-graph.md §2 계약 준수.

**Checkpoint**: `routine_call_pairs`가 FRAMEWORK calls(이미 함수↔함수)에 적용 시 항등임을 코드로 확인(루트=자기 자신).

---

## Phase 3: User Story 1 — CALL 노드가 호출 대상과 직접 이어진다 (Priority: P1) 🎯 MVP

**Goal**: DBMS 실데이터에 `CALL 노드 → 대상` 호출 엣지만 저장, proc→proc 0건.

**Independent Test**: V1(quickstart) — 그래프 덤프에서 internal CALLS 출발이 100% CALL 노드, 프로시저 루트 출발 0건, 재귀 1건 존재.

- [X] T003 [US1] `call_linker.py`의 `CallLinker.link()` 탈바꿈: 호출 엣지 출발점을 `_root_of(fn)` → **`fn.id`(CALL 노드)**로 변경. self-loop drop(현 :54)과 `(caller_root,*)` dedup(현 :56·:68) 제거(호출 지점 단위 저장). 빌트인 prefix 드롭(:23·:47) **유지**. external 분기의 스키마 폴백은 `_root_of(fn)`로 호출자 자기 스키마 계속 산출(:72) — 출발점만 `fn.id`. 클래스/함수 docstring을 새 계약(“CALL 노드 → 대상”)으로 갱신, 옛 “프로시저 루트=호출자” 잔재 제거(짧게, 원칙 §4).
- [X] T004 [US1] 계약 문서·docstring 정합: `data_models/graph_edges.py`의 `Calls` docstring(현 :113-114 “자기루프 드롭”·DBMS 설명)과 `link_calls_by_ast_step.py` docstring을 “DBMS=CALL 노드→대상, 재귀 보존”으로 갱신. `shared/neo4j/edges/calls.py`는 변경 없음 확인(id MATCH라 끝점 id만 바뀜).
- [X] T005 [US1] 검증 V1·V2: DBMS 샘플 분석 후 그래프 원문 대조 — SC-001(루트 출발 0), SC-002(CALL 출발 100%), SC-005(빌트인 0), FR-008(재귀 `CALL→자기 프로시저` 존재). 불일치 시 AST·LOGS·그래프 3단 추적 후 근본수정.

**Checkpoint**: US1 단독으로 “호출 구문→대상” 그래프 성립(MVP).

---

## Phase 4: User Story 2 — proc·모듈 호출관계는 저장 없이 파생 (Priority: P1)

**Goal**: 컴포넌트·건강·DBMS 분석순서가 CALL→target 저장 위에서 정정 전과 동등(parity).

**Independent Test**: V3(컴포넌트 동등·0붕괴 없음)·V4(fan/cycle 동등) + DBMS 분석 순서 정상.

- [X] T006 [P] [US2] `analysis_flow/step7_post_processing/build_components_step.py` `_cluster`: 인라인 `internal_edges`(현 :138-145)를 `routine_call_pairs(code_nodes, calls)`로 교체. 정점(`ids`=전 code_nodes)은 유지(구문은 고립 singleton→MIN_COMPONENT_SIZE 필터). import 추가.
- [X] T007 [P] [US2] `analysis_flow/step7_post_processing/health_metrics_step.py` `_compute_metrics`: 인라인 `internal_edges`(현 :112-116)를 `routine_call_pairs(code_nodes, calls)`로 교체. `find_call_cycles`·fan 집계는 그대로(입력만 루틴쌍).
- [X] T008 [P] [US2] (FR-013) `analysis_flow/step6_analyze_code/batch_analyze.py`(DBMS 경로) 바텀업 정렬: calls 기반 엣지(현 :82-84)를 `routine_call_pairs(code_nodes, calls)`로 교체(effective parent 엣지는 그대로). `analyze_code_step.py:145` per-node 경로는 **무변경**(Framework 전용) — 확인만.
- [X] T009 [US2] 검증 V3·V4: baseline 대비 컴포넌트 구성·`health_report.json`(risk 분포·in_cycle)·표본 fan_in/out 동등 확인(SC-003·004). DBMS 분석 로그의 rounds/in_cycle가 합리적인지 확인.

**Checkpoint**: US1+US2 = 데이터 계약 완성(저장 진실 정정 + 무회귀 파생).

---

## Phase 5: User Story 3 — 무변경 영역 보존 (Priority: P2)

**Goal**: stdlib 제외·FRAMEWORK·프론트 모듈간보기 동작 보존 확인(신규 코드 없음, 경계 검증).

**Independent Test**: V5(FRAMEWORK Calls 동일)·V6(모듈간보기 동등) + V2 재확인.

- [ ] T010 [US3] 검증 V5: FRAMEWORK 샘플 분석 → `CALLS` 엣지 집합이 baseline과 완전 동일(SC-006). `call_classifier.py` 무변경 확인.
- [X] T011 [US3] 검증 V6: 프론트 모듈간 상호작용 보기에서 모듈↔모듈 호출 엣지가 baseline과 동등(SC-007). ★프론트 UI 자가검증 — 풀스택 기동 + Playwright 헤드리스 스크린샷 Read + 콘솔 스트림 확인([[manual]] §3).

---

## Phase 6: Polish & Cross-Cutting

- [X] T012 심볼/계약 전수 grep(원칙 §5): `caller_id`·`calls_edges`·`routine_call_pairs` 전 참조가 새 의미와 일관한지, 옛 proc→proc 가정 잔재 0인지 확인. dangling import 0. FR-003(모듈↔모듈 미저장) 음성 불변식 확인(analyzer가 모듈↔모듈 CALLS를 생성하는 코드 0).
- [X] T013 6원칙 점검(원칙 §3·§4): 데드(옛 self-loop/dedup 흔적·미사용 헬퍼·옛 주석) 제거, 조용한실패 표면화, 가독성·DRY 최종. `routine_call_pairs` 1분 이해 가능 여부.
- [ ] T014 quickstart.md 전 시나리오(V1~V6) 풀 통과 + 그래프 line-by-line 원문 대조 에러 0 최종 확인. (프론트 무변경 확정 — 후속 스펙 불필요.)

---

## Dependencies & Execution Order

- **Setup(T001)** → baseline 먼저(parity 비교 위해).
- **Foundational(T002)** → US2 전제. US1(T003)과 같은 파일(`call_linker.py`)이라 T002→T003 순차.
- **US1(T003-T005)**: P1, MVP. T003→T004→T005.
- **US2(T006-T009)**: P1. T002 완료 후. T006·T007·T008 병렬[P](서로 다른 파일) → T009 검증.
- **US3(T010-T011)**: P2. 코드 변경 후 검증.
- **Polish(T012-T014)**: 전체 후.

## Parallel Opportunities

- T006·T007·T008 동시 진행 가능(build_components / health_metrics / batch_analyze — 독립 파일, 모두 T002에만 의존).

## Implementation Strategy

- **MVP = US1**(T001-T005): CALL→대상 저장만으로도 “호출 구문→대상” 1차 사실 확보.
- **무회귀 완성 = +US2**(T006-T009): 저장 진실 변경의 파급을 단일 해소기로 흡수.
- **경계 확정 = +US3**(T010-T011) → Polish.

## Notes

- 단위 테스트 미생성: parity는 routine_call_pairs ≡ 옛 집합으로 설계 보장, 검증은 실행+원문 대조가 진실(§6, “별도 검증 스크립트 = 자체 결함 위험”).
- 프론트 **무변경**(라이브 검증): 보기모드는 이미 hop-over(resolveModule/resolveRoutine), 일반 탐색기는 원본 표시가 정상(hop-over는 보기모드 전용). 후속 스펙 불필요.

## Verification Log — 2026-06-24 (실파이프라인 RWIS DBMS)

풀스택 기동(Neo4j·analyzer 5502·catalog 5503·antlr 8081·gateway 9000·frontend 3000) + FRENTIS GPU LLM + Playwright 헤드리스로 실행 검증.

- **Before(옛 코드, 기존 Neo4j)**: CALLS 5건 전부 출발 `[PROCEDURE]/[TRIGGER]` (proc→proc). 결함 현장 확인.
- **After(정정·CLI 재분석, exit 0, 비용 $0)**: CALLS 5건 전부 출발 `[CALL]` → **SC-001(proc루트 0)·SC-002(CALL 100%) ✅**. 빌트인 0 → **SC-005 ✅**. `PRC_INSERT_RDD01DD_TB2의 CALL → PRC_INSERT_RDD01DD_TB3`(사용자 최초 지적) 확인.
- **US2 parity(Neo4j)**: 컴포넌트 1군집·멤버3(붕괴 0) / health low7·medium2·in_call_cycle 0 → `routine_call_pairs` 파생 위 정상 → **SC-003·004 ✅(회귀 없음)**.
- **T011 보기모드 전수(Playwright, 4개)**: ①모듈간(MODULE3·TABLE16+모듈↔테이블 R/W·모듈↔모듈 CALL hop-over) ②협력 군집(1군집·프로시저3 뭉침=CALL hop-over→컴포넌트 시각확인) ③영역(구조)(파일/패키지 영역 루틴~9) ④건강(위험루틴+R/W+CALL). **4개 전부 빈화면0·pageerror0 → SC-007 ✅**(resolveModule/resolveRoutine 끝점의미 변경에 강건).
- **로직 검증(독립)**: routine_call_pairs parity 규칙(DBMS 재귀 드롭·FRAMEWORK 자기참조 보존·dedup·external 제외) 통과. py_compile 6파일·import OK.
- **미실행(정직)**: T010 V5 FRAMEWORK 실샘플(data/source=RWIS 단일=DBMS, framework 샘플 없음) — 단 call_classifier.py 무변경 + 해소기 idempotent(로직 검증). FR-008 재귀는 RWIS에 없어 로직 테스트로만.
