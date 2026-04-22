---
name: sql-tracking
description: >
  **언제 사용**: 사용자 메시지의 `카테고리:` 값이 `sql` 일 때 **반드시** 이 스킬을 로드할 것.
  data 에 미매칭 SQL 키(sql_key, sql_type, file_path) 가 포함됨.
  **작업 내용**: 각 sql_key 를 코드에서 추적하여 실행하는 **모든 함수** 에
  `(FUNCTION)-[:CALLS_SQL]->(SqlStatement)` 관계 생성. 테이블 접근이 드러나면 READS/WRITES 도 등록.
  **트리거 키워드**: SQL 키 매칭, MyBatis mapper, sql_lookup_tool, selectXxx/insertXxx/updateXxx
  namespace, 미매칭 SqlStatement, CALLS_SQL 엣지.
  **사용 금지 (Do NOT)**: 엔티티/클래스 추적은 `entity-analysis`, DDL 테이블 역추적은
  `table-tracking`, 설정 키 추적은 `config-tracking` 사용.
---

# SQL Tracking Skill

미매칭된 SQL 키를 코드에서 추적하여 관계를 생성한다.

## 카테고리별 처리

### category = sql

각 아이템은 `{key, target_type, sql_type, file_path}` 를 갖는다.

1. `sql_lookup_tool(key)` 로 SQL 문 전체(sql_type, sql_text, file_path) 조회.
2. 다양한 검색어로 사용처 탐색:
   - sql_key 전체: `bundle_search(sql_key, kind="text")`
   - sql_key 의 ID 부분: `bundle_search("selectUser", kind="text")`
   - 네임스페이스: `bundle_search("UserMapper", kind="symbol")`
   - 파일명: `bundle_search("UserMapper.xml", kind="text")`
   - 부분 문자열: `bundle_search("selectUser", kind="symbol")`
3. 각 검색 결과의 코드를 `bundle_get_chunk` 로 확인.
4. SQL 키를 참조하는 코드가 확인되면:
   - `graph_create_refer_to_tool("SqlStatement", sql_key, evidence, source_function_id)` — CALLS_SQL 관계.
5. (선택) SQL 문에서 테이블명이 보이면 `graph_create_table_access_tool` 등록.
6. (선택) FK 관계가 보이면 `graph_create_fk_tool` 등록.

---

## 목표

미매칭된 SQL 키(sql_key) 를 실행하는 **모든 함수** 를 찾아
`(FUNCTION)-[:CALLS_SQL]->(SQL_STATEMENT)` 관계를 생성한다.
추적 과정에서 테이블 접근이 확인되면 READS/WRITES 와 FK 도 함께 등록한다.

---

## 공통 규칙

- `source_function_id` 는 반드시 `bundle_search` 에서 발견한 실제 함수의 chunk_id 사용.
- 하나의 SQL 을 여러 함수가 사용할 수 있음 — 모두 찾아 등록.
- 검색 결과가 0건이면 검색어를 바꿔서 최소 2~3회 재시도.
- 확신이 있을 때만 등록. 근거 부족 시 생성 금지.
