# Data Model: DML 의미표

## 1. `pipeline/dml_semantics.yaml` — 스키마

```yaml
# DML 문법 의미표 — R/W 판정의 단일 진실 (spec 106).
# 표가 담는 것: 구문 데이터만. 정규식 조립·판정 로직은 pipeline/dml_semantics.py 단일 빌더.
# 새 구문 지원 = 이 파일 수정뿐이어야 한다 (헌법 II·III).

core:                          # 모든 SQL 방언 공통
  statements:
    SELECT:   {relation: READS}
    WITH:     {relation: READS, dml_type: null}   # 현행 보존 — research D3
    INSERT:   {relation: WRITES, target_after: ["INSERT INTO"]}
    UPDATE:   {relation: WRITES, target_after: ["UPDATE"]}
    DELETE:   {relation: WRITES, target_after: ["DELETE FROM", "DELETE"]}  # FROM 생략형 — D4
    MERGE:    {relation: WRITES, target_after: ["MERGE INTO"]}
    TRUNCATE: {relation: WRITES, detect: ["TRUNCATE TABLE"], target_after: ["TRUNCATE TABLE"]}
    CREATE:   {relation: WRITES, detect: ["CREATE TABLE"], target_after: ["CREATE TABLE"]}  # CTAS 포함 — D5
    # detect(선택, 기본=키 자체): 감지 구문. CREATE를 단독 감지하면 PL/SQL 파일 머리의
    # `CREATE OR REPLACE PROCEDURE`가 전부 오탐되므로 구문 단위 감지가 필수다.
  read_clauses: ["FROM", "JOIN", "USING"]   # 대상-직후 READS 추출 구문 (C 경로) — D6

dialects: {}                   # 방언 델타 — 코어와 같은 구조. 현재 필요 항목 0 (전부 코어)
                               # 예약 구조: {oracle: {statements: {...}}, postgresql: {...}}
```

### 필드 계약

| 필드 | 의미 | 규칙 |
|---|---|---|
| 키 (SELECT 등) | dml_type 값이자 기본 감지 키워드 | 대문자. `dml_type: null`이 없으면 키 자체가 dml_type |
| `relation` | READS \| WRITES | 필수. 그 외 값은 로딩 시 raise (FR-009) |
| `detect` | 감지 구문 목록 (기본 = 키 자체) | 다단어 구문 허용(`CREATE TABLE`). 긴 구문 우선 정렬은 빌더 책임 |
| `dml_type` | dml_type 덮어쓰기 | 선택. `null` = 빈 문자열(현행 보존용) |
| `target_after` | 이 구문 직후 식별자 = 대상 테이블 | WRITES에만 의미. 긴 구문 우선 정렬은 빌더 책임 |
| `read_clauses` | 이 구문 직후 식별자 = READS 테이블 | C 경로 소비. DBMS 경로는 "write 대상 외 = READS" 기본값 유지 |

## 2. `pipeline/dml_semantics.py` — 파생 API (소비자 계약)

```python
dml_semantics(dialect: str = "") -> DmlSemantics   # lru_cache 로딩, 형식 오류 즉시 raise

DmlSemantics.detect_regex        # \b(SELECT|WITH|...|CREATE)\b — 구 _DML_KW_RE·_SQL_KEYWORD_RE 대체
DmlSemantics.write_target_regex  # \b(?:INSERT\s+INTO|...|DELETE)\s+(?:[\w$]+\.)?(<ident>) — 구 _WRITE_TARGET_RE·_C_SQL_WRITE_TARGET_RE 대체
DmlSemantics.read_target_regex   # \b(?:FROM|JOIN|USING)\s+... — 구 _C_SQL_READ_TARGET_RE 대체
DmlSemantics.start_regex         # 첫 토큰 DML 판정용 — 구 _C_SQL_START_RE 대체
DmlSemantics.relation_of(keyword) -> "READS"|"WRITES"
DmlSemantics.dml_type_of(op)     # 구 derive_dml_type 대체(이사) — 표 밖 토큰은 "" (계약 유지)
```

## 3. ReadsWrites 엣지 — 변경 없음 확인

| 항목 | 상태 |
|---|---|
| 정체성 `(source_id, target_id, relation)` | 불변 |
| relation 값 집합 READS/WRITES | 불변 |
| dml_type 값 집합 | SELECT/INSERT/UPDATE/DELETE/MERGE + **TRUNCATE, CREATE 추가** (문자열 속성 — Neo4j·스트림·컨텍스트 전 소비자 pass-through, plan.md 소비자 표 참조) |
| dml_types[]·locations 축적 구조 | 불변 (reads_writes.py upsert 로직 무변경) |
