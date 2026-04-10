---
name: sql-tracking
description: >
  미매칭된 SQL 인덱스 키를 추적하여, 해당 SQL을 실행하는 함수를 찾고
  CALLS_SQL 관계를 생성한다. 테이블 접근이 확인되면 READS/WRITES도 등록한다.
  category가 "sql"인 미해결 아이템에 사용.
allowed-tools: sql_lookup_tool, bundle_search_tool, bundle_get_chunk_tool, graph_create_refer_to_tool, graph_create_table_access_tool, graph_create_fk_tool, table_search_tool, table_columns_tool
---

# SQL Tracking Skill

미매칭된 SQL 키를 코드에서 추적하여 관계를 생성한다.

## 목표

미매칭된 SQL 키(sql_key)를 실행하는 **모든 함수**를 찾아
`(FUNCTION)-[:CALLS_SQL]->(SQL_STATEMENT)` 관계를 생성한다.
추적 과정에서 테이블 접근이 확인되면 READS/WRITES와 FK도 함께 등록한다.

---

## 절차

각 미매칭 SQL 아이템에 대해:

### 1. SQL 내용 확인
- `sql_lookup_tool(sql_key)` → SQL 문 조회 (sql_type, sql_text, file_path)

### 2. 사용처 검색
다양한 검색어로 반드시 재시도:
- sql_key 전체: `bundle_search(sql_key, kind="text")`
- sql_key의 ID 부분: `bundle_search("selectUser", kind="text")`
- 네임스페이스: `bundle_search("UserMapper", kind="symbol")`
- 파일명: `bundle_search("UserMapper.xml", kind="text")`
- 부분 문자열: `bundle_search("selectUser", kind="symbol")`

### 3. 코드 확인 + 관계 등록
- 각 검색 결과의 코드를 `bundle_get_chunk`로 확인
- SQL 키를 참조하는 코드가 확인되면:
  - `graph_create_refer_to_tool("SqlStatement", sql_key, evidence, source_function_id)` — CALLS_SQL 관계

### 4. 테이블 접근 추적 (선택)
- SQL 문에서 테이블명이 보이면 → `graph_create_table_access_tool` 등록
- FK 관계가 보이면 → `graph_create_fk_tool` 등록

---

## 공통 규칙

- source_function_id는 반드시 bundle_search에서 발견한 실제 함수의 chunk_id 사용
- 하나의 SQL을 여러 함수가 사용할 수 있음 — 모두 찾아 등록
- 검색 결과가 0건이면 검색어를 바꿔서 최소 2~3회 재시도
- 확신이 있을 때만 등록. 근거 부족 시 생성 금지.
