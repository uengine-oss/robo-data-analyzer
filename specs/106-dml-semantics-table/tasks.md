# Tasks: DML 문법 의미표 — R/W 판정의 단일 진실

**Input**: Design documents from `/specs/106-dml-semantics-table/`

**Prerequisites**: plan.md, spec.md, research.md(D1~D7), data-model.md, contracts/dml-semantics-contract.md

**Tests**: 필수 — spec SC-001~005가 테스트 증거를 요구한다.

체크는 코드 작성이 아니라 **증거 확보 시점**에만 한다 (speckit 메모리 §7: 체크박스만으로 완료 단정 금지).

## Phase 1: 기준선 (변경 전 — 순서 고정)

- [x] T001 변경 전 코드로 AMS·GIOS·RWIS 코퍼스 엣지 스냅샷 채취 → `specs/106-dml-semantics-table/evidence/baseline.tsv`
      (하니스: 스크래치 경로, **라인당 CodeNode 1개** + DDL 파일 제외 — 초안이던 "파일당 1개"는
      DDL 오포함·dedup 가림 결함으로 폐기, 경위는 evidence/diff-report.md §하니스)
      **증거**: baseline.tsv 2332행, stash 역실험 채취

## Phase 2: 의미표 모듈 (Foundational — US 전체의 전제)

- [x] T002 `pipeline/dml_semantics.yaml` 신설 — data-model.md 스키마 그대로 (core 8구문 + read_clauses + dialects 예약)
- [x] T003 `pipeline/dml_semantics.py` 신설 — 로더(lru_cache, 형식 오류 즉시 raise) + 파생 API
      (detect/start/write_target/read_target regex, relation_of, dml_type_of) + dml_type_of가 구 derive_dml_type 계약(미등재="") 준수
- [x] T004 [P] `tests/pipeline/dml_semantics/test_semantics_table.py` — 로딩·파생·오류 raise·WITH null 보존·
      긴 구문 우선(DELETE FROM > DELETE)·미등재 토큰 "" (계약 4)
- [x] T005 [P] grep 게이트 테스트 — 의미표·자기 테스트 밖 DML alternation 정의 0건 (계약 1, SC-003)
      **증거**: pytest 통과 로그

## Phase 3: US1 — TRUNCATE가 쓰기로 보인다 (P1) 🎯 MVP

- [x] T006 `link_dbms_tables_step.py` 재배선 — `_DML_KW_RE`·`_WRITE_TARGET_RE` 삭제, 표 파생 소비.
      dml_type은 매치 구문별 판정(첫 키워드 단일값 계약은 유지하되 write 대상 구문의 dml_type 우선)
- [x] T007 [US1] 케이스 테스트: TRUNCATE 리터럴→WRITES+TRUNCATE / SELECT+TRUNCATE 동일 자기줄 분리 판정 /
      주석 TRUNCATE→0 / 스키마 수식 `AMS_MIG.TD_AB00008` (spec US1 AS 1~3)
      **증거**: pytest 통과 + AMS after 스냅샷에서 활성 TRUNCATE 전건 WRITES 확인

## Phase 4: US2 — FROM 생략 DELETE (P2)

- [x] T008 [US2] 케이스 테스트: `DELETE SGIOS.X`→WRITES+DELETE / `DELETE FROM t` 종전 동일 /
      `v_tab.DELETE(i)` 컬렉션 메서드 대상 추출 불발 (D4 회귀 고정) / CTAS t=WRITES·s=READS (D5)
      **증거**: pytest 통과 + GIOS after 스냅샷 READS→WRITES 전환 확인

## Phase 5: US3 — 단일 진실 (P3)

- [x] T009 `regex_table_linker.py` 재배선 — `_SQL_KEYWORD_RE`·`_C_SQL_*` 삭제, 표 파생 소비.
      read 절 USING 추가(D6 의도된 개선)
- [x] T010 `derive_dml_type` 이사 — graph_edges.py 본체 삭제, dml_semantics.py로.
      import 소비자 전수 수정(regex_table_linker·enrich_lineage_from_catalog·테스트), 옛 경로 grep 0
- [x] T011 [US3] 3소비처 일치 계약 테스트 — 같은 SQL 텍스트 DBMS/C 경로 relation 동일 (SC-005) +
      표에 테스트 구문 추가 시 3곳 동시 반영
      **증거**: pytest 통과 + 옛 심볼 grep 0 출력

## Phase 6: 전체 검증 (quickstart.md 절차)

- [x] T012 전량 pytest (FR-008 회귀 0)
- [x] T013 코퍼스 전후 diff — 의도 목록(TRUNCATE·FROM 생략 DELETE·CTAS·C USING) 밖 diff 0 (SC-004),
      SC-001(활성 TRUNCATE 전건)·SC-002(GIOS 전환) 수치 확인 → `evidence/diff-report.md`
- [x] T014 종료 감사 — git diff 전체 재독, 사용자 WIP(README.md) 미접촉, 옛 심볼·임시 파일 grep 0,
      검증됨/미검증 구분 기록 (실기동·Neo4j 대조는 런타임 가용 시 — 불가 시 미검증으로 명시)

## Dependencies

T001 → (T002~T005) → T006/T007 → T008 → T009/T010/T011 → T012~T014
T004·T005 병렬 가능. T006 이후 T009 전까지 C 경로는 구 상수 유지 상태로 전량 테스트 통과해야 함(슬라이스 단위 심문 — 원칙 §10-6).
