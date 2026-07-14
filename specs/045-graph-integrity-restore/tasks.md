# Tasks: 분석 그래프 정합성 복원 (045)

**Plan**: [plan.md](./plan.md) · **Spec**: [spec.md](./spec.md)

범례: `[P]` = 다른 태스크와 병렬 가능(파일 겹침 없음)

---

## Phase 1 — 헌법 복원 (그래프 초기화 + 조용한 실패)

- [x] **T001** [P] `shared/neo4j/schema_constraints.py` — `wipe_graph` 를 `MATCH (n) DETACH DELETE n` 로 복원. docstring 에서 **거짓 근거**("catalog `/delete/` 도 같은 규칙") 제거하고, **같은 DB 의 타 서비스 데이터도 삭제됨**을 명시. *(FR-001, FR-002, FR-003)*
- [x] **T002** [P] `shared/source_file_cache.py` — read 실패 시 `log_warning(component, "source_read_failed", 경로+원인)`. 캐시가 경로당 1회만 시도하므로 로그 폭주 없음. *(FR-004)*
- [x] **T003** `analysis_flow/step2_load_ast/load_ast_step.py` — `_with_code_text` 가 **원문 누락 노드 수**를 집계해 반환하고, `log_info(done, ...)` 에 `code_text_missing=N` 방출. 파일·라인이 **없는** 노드는 카운트에서 제외(원래 없는 것과 못 읽은 것을 구분). *(FR-005)* — T002 선행
- [x] **T004** [P] `analysis_flow/step4_linking/stage1_regex_grep/regex_table_linker.py` — 파일 read 실패 `continue` 앞에 `log_warning`. 그 파일의 R/W 엣지가 통째로 누락된다는 사실을 표면화. *(FR-006)*
- [x] **T005** [P] `analysis_flow/step2_load_ast/antlr_json_reader.py` — `_src_path` 가 `filePath` 없이 `fileName`/`stem` 으로 폴백할 때 `log_warning`. 존재하지 않는 경로를 조용히 지어내지 않는다. *(FR-007)*
- [x] **T006** `analysis_flow/step6_analyze_code/three_split_llm_fanout.py` + `analyze_code_step.py` + `batch_analyze.py` — ctx/fn 부재로 `None` 반환 시 `log_warning`. 완료 로그에 `skipped` 를 방출해 `analyzed + failed + skipped = 대상 수` 등식 성립. *(FR-008)*

## Phase 2 — 산출물 정합

- [x] **T007** ~~`table.py to_item` 에 `logical_name` + `nonempty()`~~ → **처방 오류로 정정.**
  구현 중 추적해보니 두 가지가 사실과 달랐다:
  1. `table.py` 의 `MERGE_QUERY` 는 `SET t += item` 이 **아니라 속성을 하나씩 명시 SET**(`coalesce` 로 DDL 설명 보존)한다. `nonempty()` 를 씌우면 키가 빠져 `item.x` 가 null → **기존 속성이 삭제**된다. → **FR-016 철회** (결함이 아니라 의도된 다른 메커니즘).
  2. `to_item` 은 **Neo4j 전용**이라 artifact 를 못 고친다. artifact 는 `state["tables"]` 에서 나온다.
  **진짜 원인**: `enrich_table_description_step.py` 의 `_replace_by_id(..., "analyzed_description")` 가 **단일 필드만** dataclass 로 되돌려 써서 `logical_name` 이 Neo4j 에만 남았다.
  **실제 수정**: `_replace_by_id` 를 **다중 필드**로 확장 + `logical_name` 동반 반영. `Column` 에 없던 `logical_name` 필드도 추가(Table 과 대칭 — 같은 사각지대였다). *(FR-013)*
- [x] **T007b** `shared/neo4j/nodes/table.py` — 위 사실을 docstring 에 못박아 다음 사람이 같은 오진을 반복하지 않게 함 (`nonempty` 미사용이 **의도**임을 명시).
- [x] **T008** [P] `shared/workflow/artifact_writer.py` — `analysis` 페이로드가 원문(`code_text`)을 담지 않도록 **분석 결과 필드만 투영**. `_CATEGORY` 의 유령 키 3개(`components`/`component_member_edges`/`component_calls_edges`) 제거. 머리말 파일 목록에 `table_samples.json` 반영. *(FR-014, FR-015)*
- [x] **T009** [P] `analysis_pipelines.py` — 병렬그룹 주석의 허구("components+edges" 반환) 정정. *(FR-015)*

## Phase 3 — 데이터 계보 정확성

- [x] **T010** [P] `analysis_flow/step7_post_processing/enrich_lineage_from_catalog_step.py` — `_resolve_code_id` 를 **경로 세그먼트 경계** 매칭으로 교체. 다중 매칭 시 추측 금지 → `log_warning` 후 `None`. *(FR-017, FR-018)*

## Phase 4 — 소비자(프론트) 계약 정합

레포: `d:\work\robo\project\robo-data-frontend`

- [x] **T011** [P] `src/features/code-analysis/components/graph/node-detail/VariableCard.vue` — 타입은 `var_type` 으로 읽기. 값은 `init_value || value`(변수/상수 공용 카드). `notes` 표시 추가. *(FR-009, FR-010, FR-011)*
- [x] **T012** [P] `.../node-detail/DatatypeCard.vue` — 타입 `var_type` 으로 읽기 + `notes` 표시. *(FR-009, FR-011)*
- [x] **T013** [P] `.../node-detail/FunctionCard.vue` — `notes` 표시 추가. *(FR-011)*
- [x] **T014** [P] `src/shared/utils/nodeIdentity.ts` — 루틴 라벨 집합에 `METHOD` 추가. *(FR-012)*

## Phase 5 — 스펙 정합 (supersede)

- [x] **T015** `specs/016-node-display-properties/spec.md` — 헤더에 **supersede 배너** 추가: FR-004 는 045 FR-019 로 대체됨. **본문은 보존**(역사 기록). *(FR-019)*

## Phase 6 — 검증

### 단위 검증 (완료 — 실제 실행으로 반증)

- [x] **T016** 문법·모듈 로드 전수 (`py_compile` 14파일 + 실제 import) — 통과
- [x] **T017** **SC-010** 오귀속 차단: `a.sql` vs `xxa.sql` 실행 검증 → 각각 정확히 매칭, 다중매칭은 `None`+경고 — 통과
- [x] **T018** **FR-013** `_replace_by_id` 다중 필드: `logical_name` 이 dataclass 로 반영되고 **빈값은 기존값을 안 덮음** — 통과
- [x] **T019** **FR-014** `_analysis_view` 가 `code_text` 를 제거하고 `summary` 는 보존 — 통과
- [x] **FR-015** `_CATEGORY` ↔ `WorkflowState` **양방향 대조: 유령 키 0 / 미매핑 0** — 통과
- [x] **FR-001** `wipe_graph` 쿼리에 라벨 예외 없음 (`MATCH (n) DETACH DELETE n`) — 통과
- [x] **T020** 프론트 타입체크 — 수정 파일(node-detail/*, nodeIdentity, nodeClassify, templateRouter) **에러 0건**

### E2E 검증 (미완 — 실제 분석 1회 필요)

> ⚠️ 전체 분석은 **현재 데모 그래프를 지우고 LLM 비용이 든다.** spec 046 구현까지 마친 뒤 **한 번만** 돌려 045+046 을 함께 검증한다.

- [ ] **T021** 실제 분석 1회 → SC-001(초기화 후 0) · SC-003(`code_text_missing=0`) · SC-004(합계 등식)
- [ ] **T022** SC-007(그래프↔`ast.json` 의 `logical_name` 전 건 일치) · SC-008(원문 중복 0 바이트)
- [ ] **T023** SC-005/SC-006 **화면 실물 확인** (변수 타입 행 · 메서드 파라미터)
- [ ] **T024** `check_byte_identical.py` **기준선 재생성** (FR-013/014 로 artifact 바이트가 의도적으로 바뀜)

---

## 완료 정의

- 구현 태스크 전부 체크 ✅
- 단위 검증 전부 통과 ✅
- E2E(T021~T024) 는 046 구현 후 통합 1회 실행으로 마감 ⏳
