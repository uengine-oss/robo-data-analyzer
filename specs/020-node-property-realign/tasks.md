---
description: "Task list — 노드 속성 정체화 (종류별 속성·빈값미저장·signature→params/return·notes·health정리·table_insights 분배)"
---

# Tasks: 노드 속성 정체화

**Input**: `specs/020-node-property-realign/` (plan·spec·research·data-model·quickstart·contracts)

**Tests**: 단위테스트 없음 — 검증 = 실제 파이프라인 재분석 후 노드 속성 원문 대조(quickstart V1~V7).

**경로**: 생산자 = `robo-data-analyzer/`. 소비처 = `robo-data-frontend/`(+catalog 점검).

> 원칙: 그래프=진실 / **"빈값 미저장"(`SET n += {비어있지 않은 키}`)** 단일 규칙(분기 최소) / 위험도 품질 불변 / 017·018·019 결과 유지. ★심볼(속성명) 변경마다 즉시 전수 grep(원칙 §5). 분석실행·커밋 = 사용자 OK 게이트.

> **★구현 현황 (2026-06-19, persistence-only 정정 반영)**: 코드 구현 완료·검증(py_compile·import·잔재0). 재분석(T022)·커밋(T023)만 사용자 OK 대기.
> - ✅ 공용 `shared/neo4j/props.py`(`nonempty`/`join_notes`) 신설.
> - ✅ nodes `function·variable·constant·data_type·module.py` → `SET n += {비어있지 않은 키}`, `signature`/`table_insights_json` 미저장, `comment`+`annotations`→`notes`.
> - ✅ `health_metrics_step` → 저장은 `risk_level`(+`in_call_cycle`=True시)만·루틴 라벨만. 중간지표(loc·fan·god·table_coupling) 계산·리포트는 유지, 저장 폐기.
> - ✅ 소비처 frontend: `NodeDetailBase.vue`(health 정리·risk_level/in_call_cycle)·`NvlGraph.vue`(risk_level)·`ModuleCard.vue`(notes)·`graphDisplay.ts`(notes 폴백).
> - ✅ catalog 무변경(노드 속성 read 0 = labels(n)·n{.*} 무관통과).
> - ★persistence-only 정정으로 **N/A**(필드 유지): T002(dataclass signature 제거 안 함)·T006(reader signature 유지)·T007(stream signature 표시 유지). dataclass·reader·call_linker·LLM 무손상.

## Phase 1: Setup

- [ ] T001 research A(blast)·data-model 매핑 작업노트 확정. 변경 속성명 목록(폐기: signature·annotations·comment·loc·god_flag·table_coupling·fan_in·fan_out·table_insights_json / rename: risk→risk_level·in_cycle→in_call_cycle / 신설: notes) 고정.

---

## Phase 2: Foundational (Blocking — dataclass + 저장 단일점)

**⚠️ 전 스토리가 공유. 먼저 완료.**

- [ ] T002 `data_models/graph_nodes.py` Function dataclass: `signature` 필드 제거, `annotations`+`comment` → 단일 `notes`, `params`·`return_type` 유지. docstring 정합. ★즉시 전수 grep(`\.signature`·`\.annotations`·`\.comment`) 호출부 확정.
- [ ] T003 빈값 미저장 헬퍼 컨벤션 확정: 각 nodes/*.py 가 "비어있지 않은 키만" 맵 빌드 + `MERGE (n…) SET n += $item` 패턴(단일 진실). MERGE 키(id)는 항상 포함.

**Checkpoint**: dataclass·저장 패턴 확정. US1~US3 작업 가능.

---

## Phase 3: User Story 1 — 종류별 속성·잡속성 0 (P1) 🎯 MVP

**Goal**: 노드가 자기 종류에 의미있는 속성만. 구문에 framework/health 잡속성 0.
**Independent Test**: quickstart V1·V2 — 구문 노드 keys에 annotations·params·return_type·signature·loc·god_flag·fan_* 0, 루틴엔 params·return_type·risk_level.

- [ ] T004 [US1] `shared/neo4j/nodes/function.py` `to_item`/`to_summary_item` → 비어있지 않은 키만 맵, `SET n += $item`. `signature` 적재 제거(params·return_type만), `annotations`/`comment` → `notes`. `table_insights_json` 적재 제거(US2 연동).
- [ ] T005 [P] [US1] `shared/neo4j/nodes/variable.py`·`constant.py`·`data_type.py`·`module.py` to_item → 빈값 미저장(`SET n += {비어있지 않은 키}`). 라벨(019)은 불변.
- [ ] T006 [US1] `analysis_flow/step2_load_ast/antlr_json_reader.py` `_make_function`: `signature` 폴백(node.type) 제거, `comment`/`annotations` → `notes` 매핑. `_make_*` 빈 필드는 안 채우거나 저장에서 걸러짐.
- [ ] T007 [US1] `analysis_flow/step6_analyze_function/analyze_function_step.py` 등 `fn.signature`/`fn.annotations`/`fn.comment` 의존 제거·`notes`로 정합(있으면). ★전수 grep.
- [ ] T008 [US1] `analysis_flow/step7_post_processing/health_metrics_step.py`: `_SET_QUERY` → `risk_level`(=risk)·`in_call_cycle`(=in_cycle)만, **루틴 라벨 노드에만** SET. `loc`·`god_flag`·`table_coupling`·`fan_in`·`fan_out` SET 폐기(계산 로직·리포트는 유지). fan 은 CALLS 파생 주석.
- [ ] T009 [P] [US1] 소비처 frontend `NodeDetailBase.vue`: `loc`/`god_flag`/`table_coupling`/`fan_in`/`fan_out` 표시 제거, `signature`→`params`/`return_type`, `comment`→`notes`, `risk`→`risk_level`·`in_cycle`→`in_call_cycle`.
- [ ] T010 [P] [US1] 소비처 frontend `NvlGraph.vue`: 건강색 `properties.risk`→`risk_level`.
- [ ] T011 [US1] 검증: V1·V2·V6 — 재분석 후 구문 잡속성 0·루틴 속성 정상·노드패널/건강색 회귀 0(★분석 사용자 OK 후, Playwright 자가검증).

**Checkpoint**: 종류별 속성·잡속성 0(사용자 "빈값 많다" 불만 해소).

---

## Phase 4: User Story 2 — 분석결과 제자리 (table_insights 블롭 폐기) (P2)

**Goal**: 코드노드 `table_insights_json` 폐기, 인사이트는 Table/Column `analyzed_description`(enrich)로.
**Independent Test**: quickstart V5 — `table_insights_json` 0건, 컬럼 `analyzed_description` 채워짐.

- [ ] T012 [US2] `shared/neo4j/nodes/function.py` `to_summary_item`에서 `table_insights_json` 키 제거(T004에 포함 가능). `fn.extra["table_insights"]`는 enrich 입력으로 **유지**(state).
- [ ] T013 [US2] `enrich_table_description_step`·`nodes/column.py`·`table.py` 분배 경로 무손상 확인(Table/Column `analyzed_description` SET 유지). 매칭 실패 silent 아님 확인(원칙 §IV).
- [ ] T014 [US2] 검증: V5 — 블롭 0·컬럼 analyzed_description 존재(인사이트 유실 0).

**Checkpoint**: 중복 블롭 제거, 인사이트 제자리.

---

## Phase 5: User Story 3 — 속성 용어 명확화 (P2)

**Goal**: notes 통합·risk_level/in_call_cycle 명확화 — 옛 이름 잔재 0.
**Independent Test**: quickstart V3·V4 — `notes` 존재, `annotations`/`comment`/`signature`/`risk`/`in_cycle` 잔재 0, risk_level 분포=변경전.

- [ ] T015 [US3] ★전수 grep(4영역): `signature`·`annotations`·`comment`(노드속성 의미)·`loc`·`god_flag`·`table_coupling`·`fan_in`·`fan_out`·`table_insights_json`·`\.risk\b`·`in_cycle` 옛 이름 잔재 0. notes·risk_level·in_call_cycle 정합.
- [ ] T016 [US3] 검증: V4 — `risk_level` 분포가 019 시점 `risk`와 동일(위험도 품질 불변).

**Checkpoint**: 용어 명확·옛 이름 잔재 0.

---

## Phase 6: Polish & 검증 (Cross-Cutting)

- [ ] T017 [P] 소비처 catalog `robo-data-catalog/service/**` 속성명 명시 참조(`signature`/`risk`/`fan_*` 등) grep 점검·정합(없으면 무수정 통과 확인).
- [ ] T018 [P] KV 캐시: `prompts/**` system 본문 byte-identical 유지(속성 개명이 프롬프트 무영향) — `check_byte_identical.py`/`diff -q` 0.
- [ ] T019 py_compile(analyzer 전체) + frontend 타입 무오류(변경 2파일) 확인.
- [ ] T020 doc 드리프트: README 노드 속성 서술·관련 spec(016/019) 정합.
- [ ] T021 `/speckit-analyze` 품질게이트(spec↔plan↔tasks 교차검증).
- [ ] T022 quickstart V1~V7 전체 통과 — RWIS 재분석 후 속성 원문 대조(잡속성0·notes·risk_level동일·블롭0·컬럼설명). ★분석 = 사용자 OK 후.
- [ ] T023 커밋(신원 ahnchiyoon87) — analyzer refactor / frontend main. 사용자 OK 후. requirements.txt·module_test 제외.

---

## Dependencies & Execution Order

- **Phase 2(Foundational)** = dataclass·저장패턴 선행.
- **권장 순서**: US1(종류별 속성·잡속성0, MVP) → US2(블롭 폐기) → US3(용어·잔재0 검증).
- T002 → T004·T005·T006·T007. T008(health) 독립. T009·T010(소비처)은 T002~T008 후 원자.
- Phase 6 = 전부 후. T022(검증)·T023(커밋) = **사용자 OK 후**.

### Parallel Opportunities
- US1: T005·T009·T010 [P](서로 다른 파일). Polish: T017·T018 [P].

## Implementation Strategy
- **MVP** = Phase2 + US1 → 구문 잡속성 즉시 0(사용자 핵심 불만 해소).
- 증분: +US2(블롭 폐기) → +US3(용어·잔재0) → polish·검증·커밋.
- ★속성명 변경마다 즉시 전수 grep(§5). 소비처는 생산자 후 원자(반응형 금지). 분석/커밋 = 게이트.

## Notes
- [P]=다른 파일·무의존. 회귀 0(소비처 옛 속성 가정 잔재 0).
- 검증=실제 파이프라인+원문 대조. 017·018·019 결과 불변.
