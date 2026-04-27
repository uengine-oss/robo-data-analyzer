---
name: sql-tracking
description: >
  **언제**: 카테고리 = `sql`. data 에 미매칭 SQL 키 (sql_key, sql_type, file_path) 포함.
  **작업**: 각 sql_key 를 코드에서 추적 → 실행하는 **모든 함수** 에
  `(FUNCTION)-[:CALLS_SQL]->(SqlStatement)` 관계 생성. SQL 본문에 테이블 접근이 보이면
  READS/WRITES + FK 도 함께 등록.
  **트리거 키워드**: SQL 키 매칭, MyBatis mapper, sql_lookup_tool, selectXxx/insertXxx/updateXxx,
  namespace, 미매칭 SqlStatement, CALLS_SQL.
  **사용 금지**: 엔티티/클래스 → `entity-analysis`, DDL 역추적 → `table-tracking`,
  설정 키 → `config-tracking`.
---

# SQL Tracking Skill

미매칭 SQL 키를 코드에서 추적해 CALLS_SQL 관계 + (가능하면) READS/WRITES + FK 생성.

## ⚠ 핵심 원칙

- **조기 종료 X**. 한 SQL 을 여러 함수가 호출 가능 — 빠짐없이 등록.
- 검색 0건 → 변형으로 재시도. 한 번 만에 포기 X.
- `source_function_id` 는 반드시 `bundle_search` 결과 실제 chunk_id.

## 입력 데이터

각 아이템: `{key, target_type, sql_type, file_path}`.
먼저 `sql_lookup_tool(key)` → 전체 SQL (sql_type, sql_text, file_path) 확보.

---

## 탐색 시나리오 (4가지 trail — 모두 독립 처리)

각 sql_key 마다 4가지 trail 모두 시도. 한 trail 발견했다고 다른 trail skip X.

**placeholder**:
- `<SQL_KEY>` — `namespace.methodName` 형식 SQL 키 (예: `UserMapper.selectUser`).
- `<NS>` — namespace (예: `UserMapper`).
- `<METHOD>` — 메서드명 (예: `selectUser`).
- `<funcA>`, `<funcB>` — caller 함수 chunk_id.
- `<DAO>` — DAO / 헬퍼 함수.
- `<MAPPER>` — Mapper 인터페이스.

### 시나리오 1: 직접 — sql_key 문자열 인자

**상황**: sql_key 가 함수 본문에 문자열 리터럴로 등장.

```text
[Step 1] sql_key 전체로 검색
  bundle_search(query="<SQL_KEY>", kind="text")
  → 결과: N 개 chunk_id

[Step 2] 각 chunk_id 본문 확인
  bundle_get_chunk(chunk_id="<funcA>")
  → 본문 일부:
    sqlSession.selectOne("<SQL_KEY>", params);
    또는: jdbcTemplate.execute("<SQL_KEY>")

[Step 3] 즉시 등록
  graph_create_refer_to_tool(
    target_type="SqlStatement",
    target_key="<SQL_KEY>",
    evidence='sqlSession.selectOne("<SQL_KEY>", params);',
    source_function_id="<funcA>"
  )
  → CALLS_SQL 엣지 생성.

[Step 4] (선택) SQL 본문 (sql_lookup_tool 결과) 에 테이블 / JOIN 보이면 부수적 등록
  graph_create_table_access_tool(table=<TABLE>, READS|WRITES, evidence=<SQL>, source_function_id="<funcA>")
  graph_create_fk_tool(...)  // JOIN 의 ON 조건에서
```

### 시나리오 2: namespace.method 분리 — namespace / method 별도 검색

**상황**: sql_key 가 `<NS>.<METHOD>` 형식이고 코드에서 namespace 와 method 가 분리되어 사용.

```text
[Step 1] namespace 로 검색
  bundle_search(query="<NS>", kind="symbol")
  → 결과: 인터페이스 / Mapper 정의 chunk_id

[Step 2] method 로 검색
  bundle_search(query="<METHOD>", kind="symbol")
  → 결과: 메서드 호출처 chunk_id 들

[Step 3] 각 호출처 본문 확인
  bundle_get_chunk(chunk_id="<funcB>")
  → 본문: <namespaceInstance>.<METHOD>(...);
  → 호출 확정.

[Step 4] 등록 — 호출하는 함수에
  graph_create_refer_to_tool(
    target_type="SqlStatement",
    target_key="<SQL_KEY>",
    evidence="<namespaceInstance>.<METHOD>(...);",
    source_function_id="<funcB>"
  )
```

### 시나리오 3: 헬퍼 / DAO 래퍼 경유

**상황**: DAO / 헬퍼 함수가 sql_key 를 **인자로** 받아 실행. 여러 caller 가 자기 sql_key 전달.

```text
[Step 1] 시나리오 1/2 진행 중 DAO/헬퍼 호출 패턴 발견
  본문 일부 (<funcA>):
    <DAO>.execute("<SQL_KEY>", params);   ← <DAO> 가 sql_key 받는 헬퍼

[Step 2] DAO/헬퍼 함수명으로 reverse-search
  bundle_search(query="<DAO>", kind="text")
  → 결과: DAO 정의 + caller 들

[Step 3] DAO 정의 본문 확인 (DAO 자체에는 등록 X)
  bundle_get_chunk(chunk_id="<DAO>")
  → 본문: <DAO> 가 sqlSession 등에 sql_key 그대로 전달
  → 일반 plumbing. 등록 X.

[Step 4] 각 caller 본문 확인 → 어떤 sql_key 인자로 호출?
  bundle_get_chunk(chunk_id="<funcB>")
  → 본문: <DAO>.execute("<SQL_KEY>", params);
  → 인자 sql_key: "<SQL_KEY>"

[Step 5] caller 에 등록 (DAO 자체 X)
  graph_create_refer_to_tool(
    target_type="SqlStatement",
    target_key="<SQL_KEY>",
    evidence='<DAO>.execute("<SQL_KEY>", params);',
    source_function_id="<funcB>"   ← caller!
  )
```

### 시나리오 4: @Mapper 인터페이스 메서드 경유

**상황**: MyBatis `@Mapper` 인터페이스 / `<select id="...">` XML 에 정의된 메서드를 service 가 직접 호출. sql_key 문자열은 코드에 안 보이고 메서드 이름이 sql_key 의 method 부분.

```text
[Step 1] @Mapper 인터페이스 / XML 정의 발견
  bundle_search(query="<MAPPER>", kind="symbol")
    또는: bundle_search(query="<NS>.xml", kind="text")
  → 결과: 인터페이스 / XML chunk_id
  bundle_get_chunk → 메서드 시그니처 / <select id="<METHOD>">  확인

[Step 2] mapper 인터페이스 사용처 reverse-search
  bundle_search(query="<MAPPER>", kind="text")
    또는: bundle_search(query="<mapperInstance>.<METHOD>", kind="text")
  → 결과: caller (Service / UseCase / Controller) chunk_id

[Step 3] 각 caller 본문 확인
  bundle_get_chunk(chunk_id="<callerFunc>")
  → 본문: <mapperInstance>.<METHOD>(args);

[Step 4] sql_key 매핑
  메서드명 <METHOD> 가 sql_key 의 method 부분과 매치
  → sql_key = "<NS>.<METHOD>" 또는 그 변형

[Step 5] caller 에 등록 (Mapper 자체 X)
  graph_create_refer_to_tool(
    target_type="SqlStatement",
    target_key="<SQL_KEY>",
    evidence="<mapperInstance>.<METHOD>(args);",
    source_function_id="<callerFunc>"
  )
```

### Trail 별 STOP 조건

각 trail 마다 (a) 실제 SQL 호출 함수 본문 도달, 또는 (b) 호출자 도달 → 엣지 등록 → 이 trail 만 종료. 무한 역추적 X.

---

## 안티패턴 (절대 X)

- 🚫 1개 caller 만 등록하고 종료.
- 🚫 시나리오 1 (직접) 됐다고 시나리오 2/3/4 skip.
- 🚫 DAO / Mapper 자체에 엣지 등록 (caller X).
- 🚫 같은 chunk_id 재호출.
- 🚫 근거 없이 등록.

---

## 완료 체크리스트

- [ ] 모든 sql_key 처리.
- [ ] 4가지 시나리오 모두 시도 (직접 / namespace.method 분리 / DAO경유 / Mapper경유).
- [ ] 각 sql_key 의 모든 호출자 빠짐없이 등록 (시작점 있는데 등록 0 이면 빠뜨림 의심).
- [ ] 0-hit 키워드 변형 재시도 완료.
- [ ] 근거 없는 엣지 X.

전부 YES → 완료.
