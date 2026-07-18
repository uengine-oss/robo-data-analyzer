# Tasks: 의미 AST 뼈대 보존 · Rules/Examples 폴백 · 스트림 가시성

**Input**: `spec.md`, `plan.md`, `contracts/stream-events.md`, `research.md`,
`../../antlr-code-parser/specs/007-semantic-control-flow-ast/contracts/ast-node-vocabulary.md`
**Tests**: 포함 (회귀 0·소실 0·격리 0을 명시 요구).
**전제**: parser spec 007(C)이 `IF/ELSE/LOOP/SWITCH/CASE`를 emit해야 US2 통합 검증 가능. 단위 테스트는
parser 없이 fixture JSON으로 선행 가능.

## Phase 1: Setup & 회귀 기준선

- [ ] T001 보존 run 확보 확인 — `_runs/prompt-iteration-3/`의 load_target_orders/get_code_name/calc_discount
      입출력을 회귀 표본으로 고정(읽기 전용, 훼손 금지).
- [ ] T002 제어문 포함 C AST fixture JSON 준비(parser 미완이어도 단위 테스트용) — 함수 아래 IF/LOOP/SWITCH
      중첩 + 그 아래 FUNCTION_CALL을 담은 최소 JSON을 `analysis_flow/step2_load_ast/testdata/`에 둔다.

## Phase 2: Foundational — 노드 분류 단일 진실 (US2 선행, BLOCKS)

**⚠️ CRITICAL**: 분류가 서면 나머지가 안전해진다.

- [ ] T003 `shared/lang/ontology_roles.yaml`에 C의 `IF/ELSE/LOOP/SWITCH/CASE`를 `control_structure` role로
      추가. (어휘/분류 단일 진실은 `ast-node-vocabulary.md`.)
- [ ] T004 `data_models/analysis_context.py`(또는 state)에 `analysis_target_ids`(함수만)와 함수별
      `control_subtree`(분할용) 실어 나르는 필드 추가.

**Checkpoint**: 분류 진실 확보.

---

## Phase 3: User Story 1 — 프롬프트 재구성 (P1, 우선 착수) 🎯

### Tests

- [ ] T005 [US1] `prompts/` 정적 감사 테스트/스크립트 — (a) 절대 개수 상한 문구 0, (b) 중복 문단 탐지,
      (c) good/bad 예시 존재를 검사(`test_domain_term_prompt_contract.py` 스타일 확장).

### Implementation

- [ ] T006 [US1] `prompts/framework/code_rules_examples.yaml` 전체 감사 → 중복·상충 제거, 우선순위/게이트
      1회 명시, 절대 상한 문구 제거, good/bad 예시 유지로 재구성(현재 WIP 202줄 포함).
- [ ] T007 [US1] `prompts/dbms/code_rules_examples_batch.yaml`에 Framework 공통 원칙을 배치 표현으로 반영.
- [ ] T008 [US1] `prompts/framework/code_rules_merge.yaml` 신규 — 기존 rules 지침 재사용 + 병합 전용 얇은
      지시(조각 경계 넘는 동일 Rule 합침 등). `_common.yaml` prefix byte-identical 유지.
- [ ] T009 [US1] `cli_scripts/prompt_hashes.json` 갱신 + `byte_identical` 가드 통과 확인.

**Checkpoint**: 프롬프트 일관성 확보 — 품질검사기와 같은 품질 정의 공유.

---

## Phase 4: User Story 2 — 뼈대 보존 & 분석 격리 (P1)

### Tests

- [ ] T010 [P] [US2] `test_control_structure_walk.py` — T002 fixture로: (a) IF/LOOP 보존, (b) 그 아래
      FUNCTION_CALL 소실 0, (c) IF 아래 호출의 CALLS 출발점=함수(IF 아님), (d) 구조 노드 LLM 필드=None,
      `code_text`/`owner_id`/`parent_id` 채워짐.
- [ ] T011 [P] [US2] `test_analysis_target_isolation.py` — 검색/요약/health/component 순회 소스가
      `analysis_target_ids`만이고, 구조 노드가 결과에 0건 혼입.

### Implementation

- [ ] T012 [US2] `antlr_json_reader._walk_framework`에 `owner_routine_id` 인자 추가 —
      `control_structure`는 그래프 노드 생성+자식 재귀(structural_parent=자기, owner=불변), `internal`은
      CALLS/RW를 owner_routine 기준으로, 구조 노드로도 보존(결정론 필드만). 함수에서 owner 갱신.
- [x] T013 [US2] `_make_structural_node` — `CodeNode`를 결정론 필드만 채워 생성(`code_text` 슬라이스 재사용,
      LLM 필드 미설정). 함수 `control_subtree`를 context에 실음. **현재 구조 증거(spec 069)**:
      reader가 보존한 `CodeNode.parent_id/start/end`를 step5가 함수 상대좌표 트리로 조립해
      `AnalysisContext.extra`에 주입. context/subtree/fallback 11/11, 전체 168/168 통과.
- [ ] T014 [US2] `load_ast_step.py` — `code_nodes`(전부, graph)와 `analysis_target_ids`(함수만) 분리 산출.
- [ ] T015 [US2] `analyze_code_step.py::run_framework_per_node_analysis` — `all_ids`/cycle/round를
      `analysis_target_ids`만으로. 제어문 PARENT_OF를 순서 엣지에서 제외.
- [ ] T016 [US2] **downstream 전수 감사** — 검색색인·`summarize_module`·`health_metrics`·`build_components`·
      glossary의 순회 소스를 `analysis_target_ids`로 확정(FR-013). 각 변경점 커밋 단위 기록.

**Checkpoint**: 뼈대는 그래프에, 분석은 함수만. 회귀 0.

---

## Phase 5: User Story 3 — 폴백 파이프라인 (P1)

### Tests

- [ ] T017 [P] [US3] `test_rule_quality.py` — load_target_orders 결과(60개, 중복 88%) → is_anomaly=True;
      정상 결과(중복 0) → False. 캡 도달·반복 그룹 경계 케이스.
- [ ] T018 [P] [US3] `test_ast_budget_splitter.py` — 예산 안 서브트리=통째 1조각; 초과=한 단계 재귀;
      if 조건+몸통 불가분; then/else 불합침; 작은 함수 미분할.

### Implementation

- [ ] T019 [US3] `rule_quality.py` 신규 — `check_rule_quality(rules_out, code_body) -> RuleQualityReport`
      (정규화 중복·반복 그룹·캡 도달·best-effort 미연결). 임계 시작값 research.md.
- [ ] T020 [US3] `ast_budget_splitter.py` 신규 — `split(control_subtree, code_lines, budget)` 재귀 분할.
      문자 길이 근사 예산.
- [ ] T021 [US3] `code_analysis_output.py` — `RuleItem.examples` max_length 30→80, docstring에 "안전망(품질
      판단 아님)" 명시. 상위 rules 캡 30 유지.
- [x] T022 [US3] `rules_fallback.py` 신규 — `run_rules_fallback(...)`: 감지 emit → 결정론 분할
      (`build_fragments`) → 조각별 rules LLM(주입 콜러블, 조상 조건경로) → 병합 LLM 1회(`code_rules_merge`)
      → 재검사 → 재이상 시 `deterministic_dedupe` + 이상 플래그(FR-023). BudgetExceededError 전파, 조각
      부분 실패 계속(FR-026). 증거: `test_rules_fallback.py` 7개 OK.
- [x] T023 [US3] `three_split_llm_fanout.py` — `rules_out` 확정 직후 `check_rule_quality` 훅 + 폴백
      오케스트레이션. **정상 경로는 분기 없이 통째 스킵**(is_anomaly=False → 폴백 미진입, 성능·캐시 불변).
      최종은 함수 노드에만 저장, 재이상 시 `fn.extra["rules_quality_flag"]`. 조각/병합 LLM 콜러블 팩토리
      (`_make_fragment_analyzer`/`_make_fragment_merger`)로 배관·오케스트레이션 분리. 증거: 편집 py_compile OK.
      재점검 보강: FR-023 "플래그를 함수에 저장"이 그래프까지 가도록 `code_node.to_summary_item` 에
      `rules_quality_flag` 추가(빈값 미저장 규약 — 정상경로 속성 0). 테스트 `test_flag_persists_to_graph_item`.
      ✅ **T013 생산 배선 완료(spec 069)**: `ctx.extra["control_subtree"]`가 함수 본문 상대좌표로 채워지고,
      생산 context에서 `build_fragments`가 비어 있지 않은 조각을 만드는 계약을 검증했다. 그래프의
      IF/ELSE 제어노드·PARENT_OF 계약은 그대로 유지된다.

**Checkpoint**: 이상만 폴백, 정상은 무영향. load_target_orders 재검사 통과.

---

## Phase 6: User Story 4 — 스트림 가시성 (P2)

### Tests

- [ ] T024 [P] [US4] 폴백 1건 실행 스트림 캡처 → 감지→분할→조각→병합→재검사→요약 이벤트 순서 assert
      (`test_graph_stream_source_identity.py` 스타일 확장).

### Implementation

- [ ] T025 [US4] 폴백 각 단계 `track_processing` emit(`contracts/stream-events.md` 매핑), 새
      `entity_kind="Fragment"`.
- [ ] T026 [US4] research.md coverage matrix "보강 확정" 구간(step5 context loop, fetch_table_samples,
      build_glossary, health_metrics, enrich_lineage, embed index) start/done/failed 추가.
- [ ] T027 [US4] "검토" 구간(call linking, enrich_table_description, build_components) 실제 emit 확인 후
      matrix 확정. 미보강 잔여는 사유와 함께 [[todo]] 기록.
- [ ] T028 [US4] 다건 loop 서버측 throttle/dedup(N개마다 1회) 적용(FR-028).

---

## Phase 7: 통합·정적·검증

- [ ] T029 정적 검사(ruff/기존 lint) + 단위 테스트 전체 통과.
- [ ] T030 parser 007(C) 완료 후 통합 — 실제 Shopmall C 재분석으로 SC-002/003 확인(제어문 PARENT_OF 보존,
      구조 노드 LLM 필드 None, 함수 분석 호출 수 불변).
- [ ] T031 회귀 표본 대조 — load_target_orders 폴백 통과·중복 감소, get_code_name/calc_discount 정상은
      폴백 0회. 임계 조정 시 research.md·최종 보고에 기록.
- [ ] T032 검증된 사실/미검증 범위/남은 위험 분리해 최종 보고 + [[todo]] 갱신.

## Dependencies & Execution Order

- Setup(T001-2) → Foundational(T003-4) → **US1 프롬프트(T005-9) 우선** → US2(T010-16) → US3(T017-23) →
  US4(T024-28) → 통합/검증(T029-32).
- US2와 US3는 분류·context 실어나르기(Phase 2)에 의존. US3 단위 테스트는 fixture로 US2와 병렬 가능.
- 커밋/푸시는 사용자 요청 시에만. 기존 WIP(050~053, three_split 등) 보존 — 무관 변경 되돌리기 금지.
