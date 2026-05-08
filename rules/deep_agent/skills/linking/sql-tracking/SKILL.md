---
name: sql-tracking
description: >
  **언제**: 카테고리 = `sql`. data 에 미매칭 SQL 키 (sql_key, sql_type, file_path) 포함.
  **작업**: 각 sql_key 를 코드에서 추적 → 실행하는 **모든 함수** 에
  `(FUNCTION)-[:CALLS_SQL]->(SqlStatement)` 관계 생성. SQL 본문에 테이블 접근이 보이면
  READS/WRITES + FK 도 부수적 등록.
  **트리거 키워드**: SQL 키 매칭, MyBatis mapper, sql_lookup_tool, selectXxx/insertXxx/updateXxx,
  namespace, 미매칭 SqlStatement, CALLS_SQL.
  **사용 금지**: 엔티티/클래스 → `entity-analysis`, DDL 역추적 → `table-tracking`,
  설정 키 → `config-tracking`.
---

# SQL Tracking Skill

미매칭 SQL 키를 코드에서 추적해 **CALLS_SQL** 관계 + (가능하면) READS/WRITES + FK 생성.

> 공통 절대 원칙·4-trail 탐색 원리·공통 안티패턴은 `prompts/linking/system.md` §3·§4·§5 참조. 이 SKILL 은 **이 카테고리 고유 사항** 만 명시.

---

## 1. 입력 데이터

각 아이템: `{key, target_type, sql_type, file_path}`.

먼저 `sql_lookup_tool(key)` → 전체 SQL (sql_type, sql_text, file_path) 확보.

`sql_key` 형식: `<NS>.<METHOD>` (예: `UserMapper.selectUser`).

---

## 2. 4-trail 시작 키워드 (이 카테고리 고유)

system.md 의 4-trail 적용 시 시작 키워드:

| Trail | 시작 검색어 |
| --- | --- |
| **T1. 직접** | `bundle_search(<sql_key 전체>, kind="text")` — 함수 본문에 `"<NS>.<METHOD>"` 리터럴로 등장. |
| **T2. 변수/상수 경유** | sql_key 가 상수에 담긴 경우 — 시나리오는 system.md §4 와 동일. |
| **T3. 헬퍼/래퍼 (DAO) 경유** | `bundle_search(<DAO 헬퍼명>, kind="text")` — `dao.execute("<sql_key>", ...)` 형태. |
| **T4. mapper 경유** | `bundle_search(<NS>, kind="symbol")` 로 `@Mapper` 인터페이스 확보 → `bundle_search(<NS>.<METHOD>, kind="text")` / `bundle_search(<METHOD>, kind="symbol")` 로 caller. sql_key 문자열 코드에 안 보이고 메서드 이름 = `<METHOD>` 부분. |

**namespace 와 method 분리 검색** (sql_key 전체로 hit 0 일 때): `bundle_search(<NS>, kind="symbol")` + `bundle_search(<METHOD>, kind="symbol")` 합치고 본문에서 `<namespaceInstance>.<METHOD>(...)` 패턴 확인.

---

## 3. 등록 엣지

### CALLS_SQL (주)

```text
graph_create_refer_to_tool(
  target_type="SqlStatement",
  target_key="<sql_key>",
  evidence="<호출 라인 인용>",
  source_function_id="<callerFunc>"   ← caller
)
```

### READS / WRITES + FK (부수, 가능할 때만)

`sql_lookup_tool` 결과 SQL 본문에 테이블·JOIN 보이면:

- `graph_create_table_access_tool(table=<TABLE>, READS|WRITES, evidence=<SQL 인용>, source_function_id=<callerFunc>)`.
- JOIN ON 조건 → `graph_create_fk_tool(...)`.

---

## 4. 카테고리 특유 안티패턴

- 🚫 DAO / Mapper 자체에 CALLS_SQL 등록 — caller 가 등록 대상.
- 🚫 sql_key 매칭 메서드명 추측 — `sql_lookup_tool` 결과 키와 정확히 일치할 때만 등록.

---

## 5. 완료 체크리스트 (이 카테고리 고유)

- [ ] 모든 sql_key 처리.
- [ ] 각 sql_key 의 모든 호출자 빠짐없이 등록 (시작점 있는데 등록 0 이면 빠뜨림 의심).
- [ ] SQL 본문 보이는 경우 부수적 R/W·FK 등록 검토.
- [ ] DAO / Mapper 자체에 등록 X.

전부 YES → 완료.
