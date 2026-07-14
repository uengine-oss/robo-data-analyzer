# Implementation Plan: 분석 그래프 정합성 복원 (045)

**Spec**: [spec.md](./spec.md) · **Created**: 2026-07-13 · **Status**: Ready

## Constitution Check

| 원칙 | 이 스펙과의 관계 | 판정 |
|---|---|---|
| **I. Determinism-First** | LLM 추가 0. 전부 결정론 로직·로그·이름 정합. | ✅ 준수 |
| **II. Zero-Branch Strategy** | 전략 분기 추가 없음. (기존 `== "dbms"` 분기 제거는 **046**) | ✅ 무영향 |
| **III. Single Source of Truth** | `logical_name`(그래프≠artifact), `var_type`(저장≠소비) 두 진실을 **한 벌로 복원**. | ✅ **복원** |
| **IV. No Silent Failure** | read 실패·경로조작·노드 유실의 조용한 삼킴을 **로그로 표면화**. | ✅ **복원** |
| **V. KV-Cache-Friendly** | 프롬프트 미변경. | ✅ 무영향 |
| **VI. Schema Restraint** | LLM 출력 스키마 미변경. | ✅ 무영향 |
| **VII. Submit-Tool Wrap** | 미변경. | ✅ 무영향 |
| **VIII. Cohesion Exception** | 미변경. | ✅ 무영향 |
| **Cost & Safety — full rebuild** | `wipe_graph` 를 `MATCH (n) DETACH DELETE n` 로 **복원**. | ✅ **복원** |

**결론**: 헌법 위반 신규 0건. 기존 위반 3건(III·IV·Cost&Safety)을 **복원**하는 것이 이 스펙의 목적이다. 헌법 개정 불필요.

## Cross-Service Contract Impact

헌법 "Cross-Service Contracts" 기준 판정:

| 경계 | 변경? | 영향 |
|---|---|---|
| ↑ antlr-code-parser (JSON AST 입력 포맷) | **불변** | 없음 |
| ↓ robo-architect (Neo4j 그래프 스키마: 라벨·엣지) | **라벨·엣지 불변**. 노드 **속성**만 추가(`TABLE.logical_name` 이 upsert 경로에 편입) | 없음 (추가는 하위호환) |
| ↔ Catalog (HTTP `sample-context` / `/lineage`) | **엔드포인트 계약 불변**. 응답을 코드에 매칭하는 **내부 로직**만 정확해짐 | 없음 |
| **프론트 (robo-data-frontend)** | **소비 이름 수정** — 저장 이름(`var_type`)에 소비자를 맞춤 | **동반 수정 필요** (FR-009~012) |

→ **contract 변경 0건.** 프론트는 계약을 바꾸는 게 아니라 **계약을 지키도록 고치는** 것이다 (지금이 계약 위반 상태).

## 영향 파일 (전수)

### analyzer (`d:\work\robo\project\robo-data-analyzer`)

| FR | 파일 | 변경 |
|---|---|---|
| FR-001~003 | `shared/neo4j/schema_constraints.py` | `wipe_graph` → `MATCH (n) DETACH DELETE n` + docstring 사실 정정(거짓 근거 제거, 타 서비스 삭제 명시) |
| FR-004 | `shared/source_file_cache.py` | read 실패 → `log_warning`(경로·원인). 캐시가 경로당 1회 시도라 중복 로그 없음 |
| FR-005 | `analysis_flow/step2_load_ast/load_ast_step.py` | `_with_code_text` 결과에서 **원문 누락 노드 수** 집계 → `log_info(done, code_text_missing=N)` |
| FR-006 | `analysis_flow/step4_linking/stage1_regex_grep/regex_table_linker.py` | read 실패 `continue` → `log_warning` 후 continue |
| FR-007 | `analysis_flow/step2_load_ast/antlr_json_reader.py` | `_src_path` 의 `fileName`/`stem` 폴백 시 `log_warning` |
| FR-008 | `analysis_flow/step6_analyze_code/three_split_llm_fanout.py`, `analyze_code_step.py`, `batch_analyze.py` | ctx/fn 부재 → `log_warning`. 완료 로그에 `skipped` 방출 |
| FR-013, FR-016 | `shared/neo4j/nodes/table.py` | `to_item` 에 `logical_name` 추가 + `nonempty()` 적용 |
| FR-014, FR-015 | `shared/workflow/artifact_writer.py` | `analysis` 페이로드에서 원문 제외(분석 필드만 투영) + 유령 키 3개 제거 |
| FR-015 | `analysis_pipelines.py` | 주석의 "components+edges" 허구 정정 |
| FR-017, FR-018 | `analysis_flow/step7_post_processing/enrich_lineage_from_catalog_step.py` | `_resolve_code_id` → 경로 세그먼트 경계 매칭 + 다중매칭 `log_warning` 후 드롭 |
| FR-019 | `specs/016-node-display-properties/spec.md` | 헤더에 supersede 배너 (본문 보존) |

### frontend (`d:\work\robo\project\robo-data-frontend`)

| FR | 파일 | 변경 |
|---|---|---|
| FR-009 | `src/features/code-analysis/components/graph/node-detail/VariableCard.vue` | `variable_type` → `var_type` |
| FR-009 | `.../node-detail/DatatypeCard.vue` | `variable_type` → `var_type` |
| FR-010 | `.../node-detail/VariableCard.vue` | 값 = `init_value` **또는** `value` (변수/상수 공용 카드) |
| FR-011 | `.../node-detail/FunctionCard.vue`, `VariableCard.vue`, `DatatypeCard.vue` | `notes` 표시 섹션 추가 |
| FR-012 | `src/shared/utils/nodeIdentity.ts` | 루틴 라벨 집합에 `METHOD` 추가 |

## 검증 전략

| SC | 검증 방법 |
|---|---|
| SC-001 | 분석 실행 → 초기화 직후 `MATCH (n) RETURN count(n)` = 0 |
| SC-002, SC-003 | 소스 1개 삭제 후 분석 → `analyzer.log` 에 `source_read_failed` 경고 ≥1, `done` 로그에 `code_text_missing=N` |
| SC-004 | `done` 로그의 `analyzed + failed + skipped` = 대상 노드 수 |
| SC-005, SC-006 | 실제 화면(프론트) 캡처 — 변수 타입 행·메서드 파라미터 표시 |
| SC-007 | Neo4j `TABLE.logical_name` ↔ `artifacts/ast.json` 대조 스크립트 |
| SC-008 | `ast.json`/`analysis.json` 의 `code_text` 바이트 집계 → 중복 0 |
| SC-009 | `_CATEGORY` 키 ↔ `WorkflowState.__annotations__` 양방향 대조 |
| SC-010 | 접미사 관계 파일 쌍으로 `_resolve_code_id` 단위 확인 |

## 알려진 파급

- **`check_byte_identical.py` 기준선 재생성 필요** — FR-013/FR-014 가 artifact 바이트를 의도적으로 바꾼다.
- **분석 후 카탈로그 데이터소스 재등록 필요** — FR-001 의 수용된 대가 (spec Assumptions).

## 다음 단계

`tasks.md` → 구현 → 실행검증(실제 분석 1회) → `speckit-analyze` 교차검증 → **spec 046**(DRY 구조 정리) 착수.
