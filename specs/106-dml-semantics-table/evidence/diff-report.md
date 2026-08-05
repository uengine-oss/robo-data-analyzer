# spec 106 — 코퍼스 전후 대조 증거 (T013 / SC-001·002·004·005)

**일시**: 2026-07-30 | **방법**: quickstart.md §2 — 동일 하니스(스크래치 `baseline_dml.py`)를
stash 역실험으로 변경 전/후 코드에 각각 실행, `(lane, file, target, relation, dml_type, line)` 전수 diff.

## 하니스 계약 (재현 조건)

- 입력: `분석대상모음/dbms/oracle/**/*.sql` 중 **프로시저 파일만** (경로에 `ddl` 포함·`*_tables.sql` 제외 —
  DDL 파일은 제품에서 step1 parse_ddl 입력이며 이 링커에 절대 안 들어옴).
- 입도: **라인당 CodeNode 1개** — 실제 제품의 문장 단위 노드에 근사. (1차 시도였던 파일당 1노드는
  ① DDL 파일 오포함 ② 같은 테이블 INSERT가 TRUNCATE를 정체성 dedup으로 가림 — 두 하니스 결함을
  반증 심문에서 발견하고 폐기. 그 흔적은 이 문서로 보존.)
- 테이블 목록: 코퍼스 텍스트에서 키워드 직후 식별자 전수 추출 — 전/후 동일 규칙.
- C 경로: 코퍼스에서 리터럴 SQL 해소 0건(전/후 동일) — C 판정 증거는 단위 테스트
  (`test_dml_semantics_linking.py::CSemanticParityTest`)가 담당.

## 결과 — diff 전건이 의도 목록 안 (SC-004 ✅)

| 범주 | 건수 | 판정 |
|---|---|---|
| 순추가 `WRITES/TRUNCATE` (AMS_procedures.sql) | **37** | 의도 — SC-001 |
| 변형 `READS/DELETE → WRITES/DELETE` (PRC_KAKAO_MSG_DAM·_DAM_2) | **2** | 의도 — SC-002 (FROM 생략 DELETE) |
| 순제거 | **0** | 회귀 없음 |
| 그 외 diff | **0** | — |

## SC-001 정밀 대조 — 전건·오차 0

AMS_procedures.sql의 TRUNCATE 라인을 블록주석 추적 포함 전수 분류(파이썬, 라인별):

```
활성 37건 / 주석 9건 (--, /* */) / 생성 엣지 37건
활성인데 엣지 없음: []        ← 누락 0
엣지인데 활성 아님: []        ← 오탐 0 (주석 9건 전부 미생성 — US1 AS-3)
```

## SC-005 — 두 경로 판정 일치

같은 SQL 텍스트(MERGE INTO … USING …)에 대한 DBMS 경로·C 경로 relation 동일 —
`CSemanticParityTest::test_dbms_and_c_lane_agree_on_same_sql` 통과로 증명.

## 회귀 (FR-008 / SC-004)

- 전량 pytest: **611 passed, 5 skipped, 323 subtests / 실패 1** —
  `test_graph_ownership.py::test_wipe_graph_is_owner_scoped`은 **stash 역실험으로 변경 전
  코드에서도 동일 실패 확인** (pytest-asyncio 플러그인 문제, 본 변경과 무관·사전 존재).
- grep 게이트 (SC-003): `tests/pipeline/dml_semantics/test_semantics_table.py::
  test_no_dml_knowledge_outside_semantics_table` 통과 — 의미표 밖 DML 지식 0.

## 미검증 (정직 기록 — 원칙 §6)

- 실기동 `/analyze` + Neo4j 실물 대조 + 프론트 스트림 + 권위 op 맵 렌더 육안(quickstart §3):
  이 세션에 Neo4j·LLM 런타임 미가용 → **미검증**. 다음 실기동 시 quickstart §3 절차로 확인할 것.
- PostgreSQL 방언 추가분: 의미표 구조만 예약 — 검증 범위 밖 (spec Assumptions).
