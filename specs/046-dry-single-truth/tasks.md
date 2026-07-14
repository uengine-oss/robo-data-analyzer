# Tasks: DRY·단일진실 복원 (046)

**Plan**: [plan.md](./plan.md) · **Spec**: [spec.md](./spec.md)

---

## Phase 1 — 죽은 코드 (FR-010) ✅

각 심볼을 **4개 레포 전수 grep** 해서 참조 0건임을 확인하고 삭제.

- [x] **T001** `shared/ddl/schema_resolver.py` — **파일 통째 삭제** (import 하는 파일 0)
- [x] **T002** `shared/workflow/graph_stream.py` — `emit_graph_delta` 삭제 (호출 0)
- [x] **T003** `shared/lang/ontology.py` — `is_internal` · `paradigm` 삭제 + `ontology_roles.yaml` 의 `paradigm:` 섹션(그 죽은 함수만 읽던 죽은 데이터) 삭제
- [x] **T004** `data_models/code_analysis_output.py` — `TableDescriptionsOutput` 삭제
- [x] **T005** 미사용 import — `build_context_step.py` 의 `Path` / `Any`
- [x] **T006** `shared/config/env_catalog.py` — `unwind_batch_size` · `file_concurrency` · **`mask_secrets`** 삭제.
  ★ `mask_secrets` 는 **거짓 설정**이었다: on/off 를 고를 수 있는 척했지만 소비처가 0이고 마스킹은 `cache_message_filters` 가 **무조건** 수행한다.
- [x] **T007** `.env` — 죽은 키 6개 제거 (위 3개 + `ROBO_AGENT_LINKING_MAX_CALLS` · `ROBO_PIPELINE_CALL_LLM_BATCH_SIZE` · `ROBO_PIPELINE_UNRESOLVED_BATCH_SIZE`)
- [x] **T008** `data_models/graph_edges.py` docstring 의 삭제된 `schema_resolver` 언급 제거

> **보류**: `shared/example_mapping/__init__.py` 의 재수출(`rule_hash` / `ExampleMappingResult`) — 소비처가 `builder` 에서 직접 import 하는 건 맞지만, **패키지 공개 API 표면**이라 제거 이득이 작고 위험만 있다. 죽은 코드로 분류하지 않는다.

## Phase 2 — 단일 진실: 루틴 라벨 (FR-005) ✅

- [x] **T009** `shared/lang/ontology.py` 에 **`ROUTINE_LABELS` 단일 정의처** 신설
- [x] **T010** `antlr_json_reader._ROUTINE_LABELS` · `batch_analyze._ROUTINE_TYPES` 제거 → 단일 정의처 사용
- [x] **T011** ★**숨은 소비처 발견**: `health_metrics_step.py:26` 이 `antlr_json_reader._ROUTINE_LABELS` 를 import 하고 있었다 (전수 grep 이 두 파일만 봐서 놓쳤고 **ImportError 가 잡아냈다**). 함께 단일 정의처로 전환.
  → **두 벌이 실제로 갈라져 있었다는 증거**: `health_metrics`/`antlr_json_reader` 는 `METHOD` **포함**, `batch_analyze` 는 **누락**.

## Phase 3 — 단일 진실: 확장자 (FR-003) ✅

- [x] **T012** `shared/lang/source_extension.py` 를 **지원 확장자 단일 정의처**로 재작성 — `EXT_TO_LANG`(framework) + `DBMS_EXTS` + 파생 suffix 집합 + `is_supported()`
- [x] **T013** `strategy_detector` 의 자체 목록(14종) 제거 → 단일 정의처에서 파생. **미지원 확장자만 있으면 `NoSupportedSourceError`** — 종전엔 조용히 "framework" 를 골라 **0노드 성공**으로 끝났다
- [x] **T014** `antlr_json_reader._DBMS_EXTS` 제거 → 단일 정의처 사용
- [x] **검증** `.kt` 전용 프로젝트 → 명시적 실패 / `.sql` → dbms / `.java` → framework **실행 확인**

## Phase 4 — 헌법 II: 전략 분기 0 (FR-001, FR-002) ✅

- [x] **T015** `analyze_code_step` 의 `strategy.name == "dbms"` **하드분기 제거**
- [x] **T016** framework per-node 경로를 `run_framework_per_node_analysis` 로 추출 — `run_dbms_batch_analysis` 와 **동일 시그니처**
- [x] **T017** `Strategy` 에 `code_analyzer` 필드 추가 → 두 파이프라인이 구현체 주입
- [x] **검증** 두 엔진 시그니처 **동일** 확인 / **SC-002: step 안 전략 분기 0건** (남은 grep 히트는 로그·주석·테스트뿐)
- [x] **FR-002 준수**: 엔진은 **2개 유지** — 노드 우주 정의가 실제로 다르다 (framework=contexts 전체 / dbms=skip-투명 유효트리). `if` 만 걷어냈다.

## Phase 5 — 예외 분류 단일화 (FR-011) ✅ (부분)

- [x] **T018** `shared/llm/fanout.py` 에 **`partition_llm_results`** 신설 — 예산초과=즉시 raise(회로차단기) / 그 외 예외=log+집계 / 성공=사용. 실패 정책 3종(`drop`/`keep`/`none`)
- [x] **T019** `summarize_module_step._apply_fail_policy` → 헬퍼 사용 (`keep`)
- [x] **T020** `enrich_table_description_step._apply_table_fail_policy` → 헬퍼 사용 (`none`)
- [x] **검증** 3종 정책 + 회로차단기 전파 **실행 확인**

> **정직한 범위**: 조사에서 "9곳 복붙"이라 했으나, 실제로 **같은 모양(gather→트리아지)** 인 것은 위 2곳과 `build_context`/`build_glossary`/`build_components`/`module_pre_analysis` 다. step6 의 라운드 루프는 `analyzed/failed/skipped` **집계 책임이 달라** 같은 모양이 아니다 — 억지로 합치지 않았다(FR-012 동작 불변 우선).
> 남은 4곳은 헬퍼가 이미 존재하므로 후속 정리 가능. **미완으로 남긴다.**

## Phase 6 — Neo4j 저장 통합 (FR-004) ✅ (핵심 완료)

- [x] **T021** `shared/neo4j/merge.py` 신설 — **MERGE 단일 정의처** (`merge_query` / `upsert_by_node_label` / `upsert_fixed_label`)
- [x] **T022** 라벨 그룹핑 노드 **5개**(`code_node` / `module` / `variable` / `constant` / `data_type`)의 `_merge_query` 복붙 제거 → 단일 정의처
- [x] **검증** **SC-003: 라벨 그룹핑 MERGE 쿼리 5벌 → 1벌**. 생성 쿼리 실측 확인

> **정당한 예외 (명시)**: `nodes/table.py` 는 이 경로를 **안 쓴다** — 속성을 하나씩 명시 SET 하며 `coalesce` 로 **DDL 1차 설명을 보존**해야 한다 (`SET n += item` 은 덮어쓴다). 예외임을 `merge.py` 와 `table.py` 양쪽 docstring 에 못박았다.
> **미완**: 고정 라벨 노드(`column`/`package`/`schema`/`rule`/`example`/`question`) 6종과 **엣지 14종**은 각자 MERGE 를 유지한다. `upsert_fixed_label` 은 준비해뒀다. 엣지는 SET 절이 종류마다 실제로 달라(관계 속성) 통합 이득이 작다.
> **부수 효과 확보**: 이제 소유 마커 라벨(`SET n:ANALYZED`)이 필요해지면 **`merge.py` 1줄**로 전 노드에 도장이 찍힌다 (종전엔 13곳).

## Phase 7 — 소스 캐시 (FR-007, FR-008) ✅

- [x] **T023** `executor` config 에 **run 스코프 `source_cache`** 주입
- [x] **T024** step 들이 주입받은 캐시 사용 — `load_ast_step` · `reference_linker`(step2 안 두 번째 캐시 제거) · `link_dbms_tables_step` · `call_classifier` · `build_context_step` · 두 분석 엔진
- [x] **T025** **FR-007**: `build_context_step` 이 `fn.code_text`(step2 가 이미 채운 원문)를 재사용 — 같은 슬라이스를 다시 계산하지 않는다
- [x] **검증** **SC-006: 같은 파일 16회 요청 → 실제 디스크 read 1회** 실측

> **범위 밖**: `api/source_router.py` 는 **요청 스코프**라 run 캐시와 수명이 다르다 — 그대로 둔다.

## Phase 8 — 검색 LLM 비용추적 (FR-006) ✅

- [x] **T026** `search/llm.py` 의 raw httpx 호출이 응답 `usage` 를 **표준 기록 경로**(`append_usage_record`)에 적재. 종전엔 검색(질의마다 심판·재작성 호출)의 비용이 **어디에도 안 잡혔다**
- [x] **검증** 모듈 로드 + `_record_usage` 존재 확인

## Phase 9 — AST 리더 공통 필드 (FR-009) ❌ **미완 (의도적 보류)**

- [ ] **T027** `_make_module` / `_make_code_node` / `_make_variable` / `_make_constant` / `_make_data_type` 의 **공통 8필드 5중 복제** → `_common_node_fields` 하나로

> **왜 안 했나 (정직하게)**: 이 통합은 **`label = node["type"]` 직결**을 지나간다. 그 직결이 Neo4j primary label 이자 프론트의 색·필터·카드 라우팅의 근거라, 잘못 건드리면 **화면 전체가 회귀**한다. 이번 배치에서 이미 많은 구조를 바꿨으므로, **동작 불변(FR-012)을 E2E 로 먼저 확인한 뒤** 별도로 하는 것이 안전하다.
> **가치도 상대적으로 낮다** — 5중 복제는 "새 노드 종류 추가 시 복붙" 비용이지, 지금 갈라져 있는 결함은 아니다 (확장자·루틴라벨과 달리 실제 divergence 증거 없음).

---

## 검증 현황

### 완료 (실행으로 반증)

| SC | 결과 |
|---|---|
| **SC-002** step 안 전략 분기 0건 | ✅ 0건 |
| **SC-003** 라벨 그룹핑 MERGE 쿼리 | ✅ 5 → **1** |
| **SC-004** 확장자 정의 1곳 + 미지원 시 명시 실패 | ✅ `.kt` 전용 → `NoSupportedSourceError` |
| **SC-005** 루틴 라벨 1곳 + METHOD 포함 | ✅ |
| **SC-006** 파일당 디스크 read | ✅ 16회 요청 → **1회** |
| **SC-007** 검색 LLM 비용 기록 | ✅ 적재 경로 편입 |
| **SC-008** 죽은 심볼 참조 0 | ✅ (숨은 소비처 1건은 ImportError 로 잡아 수정) |
| **SC-009** 예외 분류 정의 | ✅ 단일 헬퍼 신설 (적용 2/6 — 위 정직한 범위 참조) |
| 전체 컴파일 · 파이프라인 로드 | ✅ |

### 미완 — **E2E 1회 필요** (045 와 통합 검증)

- [ ] **SC-001 (가장 중요) — 동작 불변**: 실제 분석 1회 → 결정론 구조 라벨 분포가 리팩터 전과 **동일**해야 한다.
  **리팩터 전 기준선(실측)**: `DEFINE 164 · COLUMN 153 · FUNCTION 127 · MEMBER 55 · CONSTANT_FIELD 40 · TABLE 22 · GLOBAL_VARIABLE 17 · FILE 12 · STRUCT 9 · EXTERNAL 6 · MODULE 3`
  (`RULE`/`EXAMPLE`/`QUESTION` 은 LLM 산출물이라 run 마다 달라진다 — 비교 대상 아님.)
- [ ] 045 의 E2E (T021~T024) 와 **한 번에** 수행
