# SKILL: shopmall-c-facade

> 사내 C 쇼핑몰 코어. SQL 은 `sprintf(g_sqlbuf, "...", 값)` 로 조립 후 `db_query(g_sqlbuf,...)` /
> `db_exec(g_sqlbuf)` 로 실행. 테이블명은 호출줄이 아니라 **SQL 문자열 리터럴**(FROM/INTO/UPDATE/DELETE) 안.

## 식별 단서
- 주 언어: C. 매니페스트: `#include "shopmall.h"`, 전역 `g_ctx`/`g_sqlbuf`, 반환코드 `RET_*`.
- DB 접근: `sprintf(g_sqlbuf, "SELECT|INSERT|UPDATE|DELETE ... <table> ...", ...); db_query|db_exec(g_sqlbuf,...);`
- 모듈 간 호출: `dispatch_call("<module>","<op>",ctx)`. 외부 연동: `legacy_pg_send/legacy_courier_if/legacy_sms_send`.

## 정규식 패턴 (SQL 문자열에 앵커 — db_query 호출줄 아님)
```yaml
table_access_patterns:
  - id: shopmall_sql_insert
    call_regex: '(?P<op_token>INSERT)\s+INTO\s+(?P<entity>[A-Za-z_][A-Za-z0-9_]*)'
    op_mappings: [{token: INSERT, op: INSERT}]
    entity_source: sql_literal
  - id: shopmall_sql_update
    call_regex: '(?P<op_token>UPDATE)\s+(?P<entity>[A-Za-z_][A-Za-z0-9_]*)\s+SET'
    op_mappings: [{token: UPDATE, op: UPDATE}]
    entity_source: sql_literal
  - id: shopmall_sql_delete
    call_regex: '(?P<op_token>DELETE)\s+FROM\s+(?P<entity>[A-Za-z_][A-Za-z0-9_]*)'
    op_mappings: [{token: DELETE, op: DELETE}]
    entity_source: sql_literal
  - id: shopmall_sql_select
    call_regex: '(?P<op_token>FROM)\s+(?P<entity>[A-Za-z_][A-Za-z0-9_]*)'
    op_mappings: [{token: FROM, op: SELECT}]
    entity_source: sql_literal

ddl_transform:
  ddl_naming_rule: as_is
  fallback_prefixes: []
```

## 호출 wrapper
```yaml
call_wrappers:
  - wrapper: dispatch_call
    callee_from: first_arg_string
```

## 프레임워크 함수 (드롭 — 이 프로젝트 커스텀 래퍼/매크로만; 표준함수는 내장 stdlib 필터가 처리)
`db_query` `db_exec` `db_next_seq` `db_commit` `db_rollback` `db_encrypt` `db_decrypt`
`log_event` `LOG_INFO` `LOG_ERR` `LOG_DBG` `SAFECPY` `SAFECPY_STR`

- 드롭 금지(업무 허브): `get_code_name` `write_audit`
- 드롭 금지(외부 스텁 → EXTERNAL): `legacy_pg_send` `legacy_courier_if` `legacy_sms_send`

## codebase_notes 예시
> SQL 은 `sprintf(g_sqlbuf,"...",값)` 로 조립 후 `db_query/db_exec(g_sqlbuf)` 실행. 테이블명은 SQL 문자열
> 리터럴의 FROM/INTO/UPDATE/DELETE 뒤. **SELECT(FROM)=READ, INSERT/UPDATE/DELETE=WRITE.** 모듈간=`dispatch_call`.
