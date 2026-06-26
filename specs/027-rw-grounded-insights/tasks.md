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

## Phase C — 검증 (US1+US2)
- [ ] **T007** 단위: 필터 헬퍼(reads/writes 밖 드롭+경고 / 안쪽 유지) 테스트.
- [ ] **T008** 임포트·문법 검증.
- [ ] **T009** **재분석 1회**(깨끗한 data): COLUMN.analyzed_description 채움↑(빈값↓) · 가짜 AFFECTS_TABLE=0 · `analyzed_desc_unmatched` 경고↓ · 디스패처 함수 AFFECTS_TABLE=0 · 분류기/baseline 회귀0. (SC-001~005)

## 의존
- A·B 병렬 가능. C 는 A·B 후. 한 커밋(027).
