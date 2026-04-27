---
name: table-tracking
description: >
  **언제**: 카테고리 = `ddl` 또는 `ddl_tables`. data 에 DDL 테이블 메타 (이름·컬럼·PK·FK) 포함.
  **작업**: 각 DDL 테이블을 코드에서 역추적 → 사용하는 **모든 함수** 에 READS/WRITES 등록 +
  코드에서 새로 발견된 FK 등록.
  **트리거 키워드**: DDL 테이블 목록, CREATE TABLE, 테이블 역추적, columns/primary_keys/foreign_keys 메타.
  **사용 금지**: DDL 없이 엔티티 클래스 / 프로파일 힌트만 → `entity-analysis`. SQL 키 → `sql-tracking`,
  설정 키 → `config-tracking`.
  **주의**: 컬럼은 DDL 에서 이미 확보됨 — `graph_create_column_tool` 호출 X.
---

# Table Tracking Skill

DDL 테이블 메타에서 코드 사용처를 역추적해 READS/WRITES + FK 등록.

## ⚠ 핵심 원칙

- **조기 종료 X**. 1개 발견 후 멈춤 X — 발견되는 만큼 빠짐없이 등록.
- 검색 0건 → 변형으로 재시도 (대소문자 / 부분 / 복수형). 한 번 만에 포기 X.
- `source_function_id` 는 반드시 `bundle_search` 결과 실제 chunk_id.
- **컬럼 등록 X** (DDL 에 이미 있음).

## 입력 데이터

- 테이블 목록: `[(name, meta), ...]` 또는 `[{name, columns, primary_keys, foreign_keys}, ...]`
- `[기존 FK 목록]` 있으면 → 중복 등록 X
- `[XML / 어노테이션 엔티티 힌트]` → DDL ↔ 엔티티 매핑 참고

## 목표

1. DDL 의 **각** 테이블을 사용하는 **모든 함수** READS/WRITES 등록.
2. 코드에서 새로 발견한 FK 관계 등록 (기존 FK 와 중복 X).
3. 컬럼 등록 X.

---

## 탐색 시나리오 (4가지 trail — 모두 독립 처리)

각 DDL 테이블마다 4가지 trail 모두 시도. 한 trail 발견했다고 다른 trail skip X.

**placeholder**:
- `<TABLE>` — DDL 정의된 테이블명 그대로.
- `<funcA>`, `<funcB>` — 코드의 함수 chunk_id.
- `<KEY>` — 변수/상수 이름.
- `<HELPER>` — 헬퍼/래퍼 함수.
- `<MAPPER>` — mapper / Repository / DAO 인터페이스.

### 시나리오 1: 직접 SQL 리터럴

**상황**: SQL 문자열에 테이블명이 직접 등장.

```text
[Step 1] 테이블명으로 reverse-search
  bundle_search(query="<TABLE>", kind="text")
  → 결과: N 개 chunk_id

[Step 2] 각 chunk_id 본문 확인
  bundle_get_chunk(chunk_id="<funcA>")
  → 본문 일부:
    String sql = "SELECT * FROM <TABLE> WHERE id = ?";
    또는: "INSERT INTO <TABLE> (...) VALUES (...)"

[Step 3] READS / WRITES 판별
  SELECT → READS, INSERT/UPDATE/DELETE → WRITES

[Step 4] 즉시 등록
  graph_create_table_access_tool(
    table_name="<TABLE>",   ← DDL 정의 이름 그대로
    access="<READS|WRITES>",
    evidence="<해당 SQL 라인 인용>",
    source_function_id="<funcA>"
  )
```

### 시나리오 2: 변수 / 상수 경유

**상황**: 테이블명이 상수에 담겨 사용.

```text
[Step 1] 시나리오 1 진행 중 본 본문에서 변수 참조 발견
  본문 일부:
    static const String <KEY> = "<TABLE>";
    String sql = "SELECT * FROM " + <KEY> + " WHERE ...";

[Step 2] 변수명으로 reverse-search
  bundle_search(query="<KEY>", kind="text")
  → 결과: M 개 chunk_id

[Step 3] 각 결과 본문 → 변수가 SQL 에 쓰이는지 확인
  bundle_get_chunk(chunk_id="<funcB>")

[Step 4] 변수 사용 함수에 등록 (변수 정의 함수 X)
  graph_create_table_access_tool(
    table_name="<TABLE>",
    access="<READS|WRITES>",
    evidence='SELECT * FROM " + <KEY> + " ...  // <KEY> = "<TABLE>"',
    source_function_id="<funcB>"   ← 사용자
  )
```

### 시나리오 3: 헬퍼 / 래퍼 경유

**상황**: 일반 헬퍼가 테이블명을 인자로 받아 SQL 실행.

```text
[Step 1] 시나리오 1 진행 중 헬퍼 호출 패턴 발견
  본문 일부 (<funcA>):
    <HELPER>("<TABLE>", ...);   ← <HELPER> 가 SQL 실행 헬퍼

[Step 2] 헬퍼 함수명으로 reverse-search
  bundle_search(query="<HELPER>", kind="text")
  → 결과: K 개 chunk_id (정의 + caller 들)

[Step 3] 각 caller 본문 확인 → 어떤 테이블 인자로 호출?
  bundle_get_chunk(chunk_id="<funcB>")
  → 본문: <HELPER>("<TABLE>", ...);

[Step 4] caller 에 등록 (헬퍼 자체 X)
  graph_create_table_access_tool(
    table_name="<TABLE>",
    access="<READS|WRITES>",
    evidence='<HELPER>("<TABLE>", ...);',
    source_function_id="<funcB>"   ← caller
  )
```

### 시나리오 4: mapper / repository / DAO 경유

**상황**: ORM mapper / Repository 가 테이블 추상화.

```text
[Step 1] mapper / Repository 정의에서 <TABLE> 매핑 확인
  bundle_search(query="@Table(name=\"<TABLE>\")", kind="text")
    또는: bundle_search(query="@Entity", kind="text")  → 본문에서 <TABLE> 매핑 찾기
  → 매핑된 엔티티 클래스 식별 (예: <Entity>)

[Step 2] Repository / DAO 호출처 reverse-search
  bundle_search(query="<entityRepo>.<method>", kind="text")
    또는: bundle_search(query="<MAPPER>", kind="symbol")
  → caller 함수들 발견

[Step 3] 각 caller 본문 확인
  bundle_get_chunk(chunk_id="<callerFunc>")
  → <entityRepo>.findById, .save, .update 등 메서드 호출 보임

[Step 4] 메서드명 → READS/WRITES 결정
  find*/get*/select* → READS, save*/update*/delete* → WRITES

[Step 5] caller 에 등록 (Repository 자체 X)
  graph_create_table_access_tool(
    table_name="<TABLE>",
    access="<READS|WRITES>",
    evidence="<entityRepo>.<method>(...);",
    source_function_id="<callerFunc>"
  )
```

### Trail 별 STOP 조건

각 trail 마다 (a) 실제 SQL 실행 함수 본문 도달, 또는 (b) 호출자 도달 → 엣지 등록 → 이 trail 만 종료. 무한 역추적 X.

---

## FK 추적 (테이블 엣지 등록 후)

```text
[Step 1] CREATE TABLE 검색 → REFERENCES 절 추출
  bundle_search(query="CREATE TABLE", kind="text")
  → 본문에서 "REFERENCES <PARENT_TABLE>(<col>)" 패턴 추출

[Step 2] SQL JOIN 검색 → ON 조건 분석
  bundle_search(query="JOIN", kind="text")
  → "JOIN <TABLE> ON ... = ..." 에서 FK 추출

[Step 3] 컬럼 명명 관례
  table_columns_tool(<TABLE>) → 컬럼 목록
  → "<xxx>_id" 패턴 → FK 후보
```

### FK 등록 규칙

- `table_search_tool` / `table_columns_tool` 로 양쪽 검증 후 등록.
- `from_table` = FK 컬럼 쪽 (자식), `to_table` = PK 쪽 (부모).
- 자기 참조 X. 중복 X. **기존 FK 목록에 있으면 X.**

---

## 안티패턴 (절대 X)

- 🚫 컬럼 등록 (이미 DDL 에 있음).
- 🚫 1개 caller 만 등록하고 종료.
- 🚫 시나리오 1 (직접) 됐다고 시나리오 2/3/4 skip.
- 🚫 헬퍼 / Repository 자체에 엣지 등록 (caller X).
- 🚫 같은 chunk_id 재호출.
- 🚫 근거 없이 등록.

---

## 완료 체크리스트

- [ ] DDL 의 모든 테이블 처리.
- [ ] 4가지 시나리오 모두 시도 (직접 / 변수경유 / helper경유 / mapper경유).
- [ ] 각 테이블 사용 함수 빠짐없이 등록 (시작 테이블이 있는데 등록 0 이면 빠뜨림 의심).
- [ ] 0-hit 키워드 변형 재시도 완료.
- [ ] FK 등록: 기존 FK 와 중복 X.
- [ ] 컬럼 등록 X (DDL 보유).

전부 YES → 완료.
