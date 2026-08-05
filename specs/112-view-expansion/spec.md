# Feature Specification: 뷰 펼치기 — VIEW 엣지에 기저 테이블 추가

**Created**: 2026-07-30 | **Status**: Implemented (동세션)

## 무엇/왜
코드가 뷰를 읽으면 종전엔 뷰까지만 기록 — 기저 테이블(뷰의 레시피)이 안 보였다.
step1 이 버리던 `CREATE VIEW … AS SELECT` 원문을 `Table.view_definition` 으로 보관하고,
링킹 후 그 정의를 **DML 의미표(spec 106)** 로 재판정해 기저 READS 엣지를 추가한다.

## 결정
- sqlglot lineage 대신 의미표 재사용 — 판정 로직 단일 진실 유지, 방언 민감성 회피.
  컬럼 단위 요구가 생기면 sqlglot(이미 의존성) 승격 — 그때 별도 spec.
- 원본 뷰 엣지 유지(직접+경유 둘 다), evidence 는 코드 위치 승계.
- 중첩 뷰 깊이 3·순환 차단. 뷰 WRITES 는 정의상 write 대상 기저에만 전파(그 외 READS 보수).
- 두 레인 동일 배선(link_dbms_tables·link_deterministic 공용 `expand_view_edges`) — 분기 0.
- 실DB-발견 테이블(spec 107)의 뷰 정의 조회는 비범위 — DDL 유래 뷰만 (fabric schema API 에
  정의 SQL 필드가 없음 확인 시 확장 별건).

## 검증
tests/pipeline/step5_linking/test_view_expansion.py **5 passed** — 기저 READS / 중첩 전개 /
순환 안전 / updatable view 보수 전파 / 일반 테이블 불변. 전량 green.
