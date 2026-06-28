# Tasks: 027-rw-grounded-insights

**입력**: spec.md · plan.md. 분류/매칭·스키마 불변. dbms·framework 공통.

## Phase A — 컬럼 주입 (US1, #4)
- [ ] **T001** `analyze_code_step.py`: `_build_table_contexts(tables, table_sample_data, columns_by_table)` 로 확장 — 테이블별 DDL 컬럼명 목록을 컨텍스트 문자열에 `| 컬럼: …` 추가. 호출부에서 `state["columns"]`→`{table_id:[col.name]}` 구성해 전달.
- [ ] **T002** 프롬프트 `prompts/framework/code_table_insights.yaml` + `prompts/dbms/code_table_insights_batch.yaml`: "주어진 컬럼 목록 기준으로, 코드(struct 멤버 포함)에서 실제 쓰는 컬럼을 빠짐없이 description. 목록 밖 추측 금지." (시스템 본문 = 1회 캐시리셋)

## Phase B — 테이블 귀속 제약 (US2, #5)
- [ ] **T003** 공통 헬퍼: per-함수 `em.affects_table`(+table_insights table)에서 table 의 bare-name 이 `ctx.reads∪writes`(bare) 밖이면 드롭. 드롭 발생 시 `log_warning`(FR-005).
- [ ] **T004** `three_split_llm_fanout.py`(framework) per-함수 조립부에 T003 필터 적용.
- [ ] **T005** `batch_analyze.py`(dbms) per-함수 조립부에 T003 필터 적용.
- [ ] **T006** 프롬프트 `prompts/framework/code_rules_examples.yaml` + `prompts/dbms/code_rules_examples_batch.yaml`: "callee(하위호출)는 테이블 아님. AFFECTS_TABLE = 이 함수가 직접 R/W 하는 테이블만."

## Phase D — FR-005 드롭 표면화 심각도 정밀화 + 집계 표면화 (2026-06-28 갱신)
- [x] **T010** `rw_table_filter.py` `constrain_tables_to_rw` 로깅: `allowed` 비면(=디스패처 예상 드롭) per-함수 `log_info`(analyzer.log) + run 집계 누적(`_drop_stats`), 비지 않은데 밖 테이블이면(=진짜 오귀속) `log_warning`(error.log). 필터 드롭 판정·반환값·프롬프트·스키마 불변. dbms·framework 공통.
- [x] **T010a** `rw_table_filter.py` run-scoped 집계 `reset_drop_stats`/`flush_drop_summary` 추가 — flush 가 디스패처 예상 드롭을 error.log **1줄**(`table_attr_off_rw_dispatcher`, N함수 M건)로 표면화 후 초기화(FR-005a). `analyze_code_step.py`: step6 시작 reset + 종료 flush(framework·dbms 양 경로).
- [x] **T011** `test_rw_table_filter.py`: 디스패처→INFO / 오귀속→WARNING 분기 + 집계 누적·flush 1줄·드롭0 무음(SC-007) 검증. (8/8 통과)
- [x] **T010b** 실제 파일 핸들러 결정론 검증(라우팅+집계): ① 디스패처 per-함수 error.log 0 ② 집계 error.log 정확히 1줄 ③ 오귀속 error.log WARNING ④ per-함수 상세 analyzer.log. **ALL PASS**. step6 임포트(순환참조 0) OK.
- [x] **T012** 회귀 재분석 완료(라이브, 이번 run 디스패처 드롭 1건 실발생): 파이프라인 정상 종료(38/38 분석) · 데이터 불변(b000·c300 AFFECTS_TABLE=0) · **error.log = 집계 1줄만**(`table_attr_off_rw_dispatcher: …1함수 1건`, per-함수 디스패처 0줄) · per-함수 상세는 analyzer.log INFO. SC-006·SC-007 충족, 회귀 0.

## Phase C — 검증 (US1+US2)
- [ ] **T007** 단위: 필터 헬퍼(reads/writes 밖 드롭+경고 / 안쪽 유지) 테스트.
- [ ] **T008** 임포트·문법 검증.
- [ ] **T009** **재분석 1회**(깨끗한 data): COLUMN.analyzed_description 채움↑(빈값↓) · 가짜 AFFECTS_TABLE=0 · `analyzed_desc_unmatched` 경고↓ · 디스패처 함수 AFFECTS_TABLE=0 · 분류기/baseline 회귀0. (SC-001~005)

## 의존
- A·B 병렬 가능. C 는 A·B 후. 한 커밋(027).
