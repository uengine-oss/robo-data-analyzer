# spec 126 — 공통 데이터 객체 사용 증거

## 문제

- DBMS linker는 DML source 범위에서 DDL의 bare table name을 정규식 검색해 READS/WRITES를
  연결한다. schema, database link, alias와 qualified column 소유권은 소실된다.
- table-insight prompt는 table 목록과 SQL 원문만 받아 alias→column 관계를 모델이 다시
  추론한다. 첫 오좌표 `PRC_INSERT_RDD01DD_TB_WATERSUISUL.sql:239-245`에서 이 손실이
  `RDIB2EN_TB`에 존재하지 않는 `VAL` 의미를 섞는 upstream 오염으로 이어졌다.
- Framework와 DBMS가 서로 다른 데이터 객체 표현을 생산하면 Catalog 해소, GWT 근거와
  table 의미가 다시 분기된다.

## 채택 계약

1. 공통 `DataObjectUsageEvidence`는 source object identity
   `(rawReference, schema, name, databaseLink)`, alias, access, 정확한 파일/행과
   명시 수식 column을 보존한다.
2. DBMS는 parser AST 증거를 소비한다. source regex는 새 의미 생산 경로가 아니며,
   parser 증거가 없는 보존 corpus의 명시적 legacy fallback으로만 격리한다.
3. Framework의 기존 R/W/column 사실도 같은 공통 계약으로 투영한다. 언어별 최종 table
   description 경로는 만들지 않는다.
4. resolver는 exact qualified identity를 우선하고 Catalog가 확정하지 못한 객체는
   `OBJECT_REFERENCE`로 보존한다. bare-name guessed binding을 확정 사실로 쓰지 않는다.
5. table-insight와 후속 table-description에는 현재 table에 결정론적으로 귀속된 alias와
   qualified column만 전달한다. 모델은 객체 소유권을 다시 선택하지 않는다.
6. 기존 TABLE/COLUMN, READS/WRITES와 2단계 table 의미 생성 경로는 유지한다.

## 비범위

- DBMS 재분석 실행(현재 실행 상한 0회)
- synonym/search_path/current schema 정책의 임의 확정
- alias 없는 컬럼의 통계적 소유권 추정
- DBMS만을 위한 별도 최종 table-description bypass

## 성공 기준

- 첫 오좌표에서 `A` 컬럼은 RDIB2EN_TB, `B` 컬럼은 RDD01DD_TB로 prompt 이전에 확정된다.
- schema/link/alias/qualified-column가 AST→CodeNode→AnalysisContext→prompt까지 손실 0이다.
- nested query와 self join에서 중복 또는 부모 오귀속이 없다.
- parser 증거 미지원 입력은 조용히 guessed binding하지 않고 provenance가 드러난다.
- 기존 table graph와 전체 analyzer test에 무회귀가 있다.

