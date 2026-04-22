---
name: table-tracking
description: >
  **언제 사용**: 사용자 메시지의 `카테고리:` 값이 `ddl` 또는 `ddl_tables` 일 때 **반드시**
  이 스킬을 로드할 것. DDL 테이블 메타데이터(이름·컬럼·PK·FK) 가 data 에 포함됨.
  **작업 내용**: 주어진 각 DDL 테이블을 **모두** 대상으로 bundle_search 하여 이 테이블을
  사용하는 **모든 함수** 에 READS/WRITES 엣지 등록, 코드에서 새로 발견된 FK 등록.
  **트리거 키워드**: DDL 테이블 목록, CREATE TABLE, 테이블 역추적, column/primary_key/foreign_key
  메타데이터, 기존 FK 중복 방지.
  **사용 금지 (Do NOT)**: DDL 없이 엔티티 클래스나 프로파일 힌트만 있을 때는 `entity-analysis`.
  SQL 키 추적은 `sql-tracking`, 설정 키 추적은 `config-tracking` 사용.
  **주의**: 컬럼은 DDL 에서 이미 확보됨 — `graph_create_column_tool` 호출 금지.
---

# Table Tracking Skill

DDL 테이블 목록을 기반으로 코드 내 사용처를 역추적한다.

## 카테고리별 처리

### category = ddl · ddl_tables

데이터는 DDL 테이블 메타의 리스트 또는 미매칭 DDL 키 아이템이다.

데이터에 포함될 수 있는 필드:
- 테이블 목록: `[(name, meta), ...]` 또는 `[{name, columns, primary_keys, foreign_keys}, ...]`
- `[이미 식별된 FK]` 목록이 있으면 **해당 FK 중복 생성 금지**.
- `[XML/어노테이션 엔티티 힌트]` 가 있으면 DDL↔엔티티 매칭에 참고.

---

## 목표

1. DDL 에 정의된 각 테이블을 사용하는 **모든 함수** 를 찾아 READS/WRITES 등록.
2. 코드에서 발견되는 **새로운 FK 관계** 를 등록 (기존 DDL FK 와 중복 금지).
3. **컬럼은 DDL 에서 이미 확보됨** → `graph_create_column_tool` 사용 금지.

---

## 절차

각 테이블에 대해:

1. 테이블명으로 `bundle_search(kind="text")` → 이 테이블을 사용하는 함수 탐색.
2. 각 함수의 코드를 `bundle_get_chunk` 로 확인 → READS/WRITES 판별.
3. 빠짐없이 `graph_create_table_access_tool` 호출 (`table_name` 은 DDL 에 정의된 이름 그대로).

### READS/WRITES 판별 기준

**READS**: SELECT 문, 조회 함수 호출 (`_s` 접미사, find, get, select, query 등)
**WRITES**: INSERT/UPDATE/DELETE 문, 변경 함수 호출 (`_i`, `_u`, `_d` 접미사, save, insert, update, delete 등)

- 반드시 코드를 읽어서 확인. 추측 금지.
- `evidence` 에 구체적인 SQL 문 또는 함수 호출 코드를 기재.
- 하나의 함수가 같은 테이블에 READS 와 WRITES 모두 할 수 있음.

---

## FK 추적

테이블 추적이 끝난 후:

1. **코드 내 DDL**: `bundle_search("CREATE TABLE", kind="text")` → `REFERENCES` 절 확인.
2. **SQL JOIN 패턴**: 추적 과정에서 발견된 `JOIN ... ON` 구문에서 FK 추출.
3. **필드명 관례**: `table_columns_tool` 로 컬럼 확인 → `xxx_id` 패턴 필드 → FK 후보.

### FK 등록 규칙

- `table_search_tool`, `table_columns_tool` 로 양쪽 테이블·컬럼 반드시 검증 후 등록.
- `from_table`: FK 컬럼 쪽(자식), `to_table`: 참조되는 PK 쪽(부모).
- 자기 자신 FK 금지, 중복 등록 금지.
- **이미 식별된 FK 목록이 주어지면 중복 생성하지 말 것.**

---

## 공통 규칙

- 컬럼은 DDL 에서 이미 확보됨 → `graph_create_column_tool` 사용 금지.
- 하나의 함수를 찾았다고 멈추지 말고 모든 사용처를 빠짐없이 찾을 것.
- 검색 결과가 0건이면 검색어를 바꿔 최소 2~3회 재시도 (부분 문자열, 대소문자, 복수형 등).
- 확신이 있을 때만 등록. 근거 부족 시 생성 금지.
