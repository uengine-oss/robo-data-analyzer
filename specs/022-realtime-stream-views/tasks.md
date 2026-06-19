# Tasks: 실시간 분석 진행 스트림 + 전략무관 일관 보기모드

**Spec**: [spec.md](./spec.md) | **Plan**: [plan.md](./plan.md) | **Date**: 2026-06-19

**레포**: analyzer(`refactor`) + frontend(`main`). 계약(NDJSON)=형식 불변(시점만 확대).

**경로 약어**: `A/` = `robo-data-analyzer/` · `F/` = `robo-data-frontend/`

---

## Phase 1: Setup

- [ ] T001 풀스택 기동·데이터 확인 — Neo4j 7687 · antlr 8081 · gateway 9000 · catalog 5503 · analyzer 5502 · frontend 3000(HMR). data/{source,analysis,ddl}/RWIS 존재 확인. ★gateway→catalog 모든 `/robo/*` HTTP500 상태면 라우트/재기동으로 선복구(라이브검증 선결, 스펙 밖이나 필수).

---

## Phase 2: Foundational (★US1 전 차단 — 모든 백엔드 emit의 토대)

- [ ] T002 `A/shared/workflow/graph_stream.py` 신규 — 공통 emit 헬퍼. `emit_graph_delta(stream, nodes, edges)`: 노드 dataclass→`{op:"merge",label,id,props(빈값제외 표시속성)}`, 엣지 dataclass→`{op:"merge",rel_type,from:{id},to:{id},props}`. 노드 label/id 추출·엣지 끝점 id(역할명 필드)는 graph_nodes/graph_edges dataclass 실제 필드로 확정(전수 확인 후). stream None이면 no-op.
- [ ] T003 `A/analysis_flow/step6_analyze_code/analyze_code_step.py` `_emit_code_graph` → T002 헬퍼 재사용으로 리팩터(DRY). EM 전용(RULE/EXAMPLE/QUESTION·HAS_RULE·NEXT·AFFECTS_TABLE 등 name 끝점)은 기존 유지, 코드노드 node_event는 헬퍼 경유. **동작 동일 검증**(같은 wire 산출).

**Checkpoint**: 헬퍼 완성 + step6 회귀 0 → US1 step emit 병렬 가능.

---

## Phase 3: User Story 1 — 분석 진행 실시간 (Priority: P1) 🎯 MVP

**Goal**: 각 비-LLM step 완료 즉시 그 산출물(노드·관계)·message가 화면에 라이브로 채워짐.

**Independent Test**: 재분석 시 첫 시스템 step 완료 시점에 트리/그래프에 노드 등장 + 콘솔 message + 관계 수 0 아님.

- [ ] T004 [P] [US1] `A/analysis_flow/step1_parse_ddl/parse_ddl_step.py` — 완료 시 TABLE·COLUMN 노드 + HAS_COLUMN 관계 전량 헬퍼로 emit + `message` ("✅ DDL 파싱 완료 — 테이블 N, 컬럼 M").
- [ ] T005 [P] [US1] `A/analysis_flow/step2_load_ast/load_ast_step.py` — FILE·CLASS·PROCEDURE·METHOD·구문(SELECT/IF/…)·VARIABLE·PARAMETER 노드 + PARENT_OF·HAS_MEMBER·BELONGS_TO 관계 emit + `message`.
- [ ] T006 [P] [US1] `A/analysis_flow/step3_scan_project/scan_codebase_step.py` — (framework) 코드노드 산출분 emit + `message`. 기존 stream_emit 인자와 중복 없이 정합.
- [ ] T007 [P] [US1] `A/analysis_flow/step4_linking/stage1_regex_grep/link_deterministic_step.py` + `stage2_extract_calls/link_calls_by_name_step.py` + `stage3_residual_patterns/link_residual_patterns_step.py` — (framework) READS·WRITES·CALLS·REFERENCES·INIT_BY 산출 관계 emit + step별 `message`.
- [ ] T008 [P] [US1] `A/analysis_flow/step4_linking/link_dbms_tables_step.py` + `stage2_extract_calls/link_calls_by_ast_step.py` — (dbms) READS·WRITES·CALLS 산출 관계 emit + `message`.
- [ ] T009 [US1] `F/src/features/code-analysis/analyzer.ts` — 콘솔: `message` 렌더 확인(기존 logToConsole). 더 세밀한 라이브감 필요 시 `doAnalyze` switch에 `processing_event` 핸들러 1줄 추가(콘솔 보조). HIDDEN 정책 불변 확인(골격 표시관계 비HIDDEN).
- [ ] T010 [US1] 검증 — 재분석 CLI(`A/cli_scripts/analyze_data_dir.py RWIS --strategy dbms`) 실행 중 `logs/analysis.run/latest/analyzer.log`에 step1~4 골격 node_event/relationship_event가 step 완료 시점에 나타나는지 + 최종이 getGraphData와 정합(중복·누락 0). id 일관(골격↔step6 병합) 확인.

**Checkpoint**: US1 단독으로 라이브 진행 표시 동작(MVP).

---

## Phase 4: User Story 2 — 보기모드 전략무관 일관 (Priority: P2)

**Goal**: 영역/건강/모듈간이 대량 그래프도 펼쳐 보이고 framework·dbms 동일 동작.

**Independent Test**: 분석 완료 후 3보기 각각 노드 펼침·헐·색강조 정상, dbms·framework 동일.

- [ ] T011 [US2] `F/src/features/code-analysis/components/graph/NvlGraph.vue` — `NVL_OPTIONS.layoutOptions` 대량 그래프 정상 배치되도록 수정(프로토타입 `F/doc/영역보기-NVL위에-D3오버레이-reference.html` 정합: 기본 forceDirected, `physics.enabled=false`·`updateLayoutOnChange:false` 배치차단 제거/완화). 빈화면·뭉침 0.
- [ ] T012 [US2] `F/src/features/code-analysis/components/graph/useGroupHulls.ts` — 레이아웃 정착 중 헐이 노드 따라가게 연속 추종 보강(필요 시 onLayoutStep/rAF). world→screen 변환은 이미 프로토타입 동일(불변).
- [ ] T013 [US2] 전략 일관 확인 — `A/analysis_pipelines.py` 양 전략 build_components/health_metrics 공통(component_id/risk_level 같은 키, 코드 확인됨). 보기모드 로직 1벌 유지(전략 분기 추가 금지). 모듈간 알고리즘(`F/.../graphCanvas.ts`)은 타입집합+PARENT_OF로 전략 무관 — 변경 없이 양 전략 검증 대상으로만.
- [ ] T014 [US2] 검증 — 라이브(분석 완료 후) 모듈간 25노드급 펼침·영역 헐·건강 색을 Playwright 스샷으로 확인. ★드래그-드롭 1-hop 무회귀(SC-007) 동시 확인.

**Checkpoint**: 보기모드 3종 + 드래그-드롭 회귀 0.

---

## Phase 5: User Story 3 — 카운트 정확 (Priority: P3)

**Goal**: 진행 패널 카운트 = 실제 표시 고유 노드/관계 수.

**Independent Test**: 패널 "노드 N·관계 M" = 오버뷰 "총 X·Y" 일치.

- [ ] T015 [US3] `F/src/features/code-analysis/components/graph/GraphTab.vue` — `graphStats`를 `graphEvents` 개수 → `projectStore.graphData`(nodeMap/linkMap 숨김 제외) `nodes.length`/`links.length` 기준으로 교체. nodeTypes/relTypes도 graphData 파생. (T011/T012와 다른 파일이라 [P] 가능하나 GraphTab 단독.)

**Checkpoint**: 카운트 100% 일치.

---

## Phase 6: Polish & 교차검증

- [ ] T016 [P] analyzer 정적검증 — 변경 step·헬퍼 `py_compile`/`compileall`/import. 프롬프트 미변경(byte-identical 영향 0).
- [ ] T017 framework 샘플 1회 재분석 — 영역/건강/모듈간이 dbms와 동일 동작(SC-006) 라이브 확인.
- [ ] T018 라이브 자가검증 전체 — quickstart.md 시나리오 A~D(SC-001~007) Playwright 스샷 + 그래프 쿼리 대조. 결과를 작업보고에 기록.
- [ ] T019 [P] 데드/잔재 정리(인라인) — 변경 중 발생한 미사용·옛 분기·중복 emit 제거. 핸드오프 문서·[[todo]] 갱신.

---

## Dependencies & 실행 순서

```
Setup(T001)
  └─ Foundational(T002 헬퍼 → T003 step6 리팩터)   ← ★차단
       ├─ US1(T004,T005,T006,T007,T008 [P]) → T009 → T010
       ├─ US2(T011,T012 → T013 → T014)
       └─ US3(T015)                                ← US1/US2와 독립
  Polish(T016~T019) — 각 US 완료분 위에서
```

- **MVP = US1**(T001~T010): 라이브 스트림만으로도 핵심 가치 전달.
- US2·US3는 US1과 독립(다른 파일·관심사) — 백엔드(US1) vs 프론트 렌더(US2)·카운트(US3) 병렬 가능.
- ★주의: T011(NvlGraph)·T012(useGroupHulls)·T015(GraphTab)는 서로 다른 파일이라 [P]지만, 같은 그래프 컴포넌트군이라 순차 검증 권장.

## 병렬 실행 예시

- 헬퍼(T002·T003) 완료 후: T004·T005·T006·T007·T008 동시(서로 다른 step 파일).
- 프론트: T011·T015 동시 가능(NvlGraph vs GraphTab 다른 파일).

## 게이트 (사용자 OK 후)

- 최종 재분석(wipe) + 라이브 전수검증(T018) → 커밋(analyzer `refactor`·frontend `main`, 신원 ahnchiyoon87, requirements.txt·module_test 제외). architect 영향 0(스키마·계약 불변).
