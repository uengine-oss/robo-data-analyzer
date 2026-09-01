# SKILL: shopmall

## Match when

- C 소스에 `shopmall.h`, 전역 `g_ctx`/`g_sqlbuf`, `RET_*` 반환코드가 있다.
- SQL을 `sprintf(g_sqlbuf, ...)`로 만들고 `db_query`/`db_exec`로 실행한다.
- 모듈 호출은 `dispatch_call("<module>", "<op>", ctx)` 형태다.

## ScanResult contract

매칭되면 아래 YAML 필드를 그대로 사용한다. 패턴을 합치거나, 테이블명을 나열하거나,
대안 순서를 바꾸거나, 이름을 정규화하지 않는다.

```yaml
table_access_rules:
  - kind: sql_fragment
    id: shopmall_sql_insert
    keyword: INSERT
    before_entity: INTO
    op_mappings: [{token: INSERT, op: INSERT}]
  - kind: sql_fragment
    id: shopmall_sql_update
    keyword: UPDATE
    after_entity: SET
    op_mappings: [{token: UPDATE, op: UPDATE}]
  - kind: sql_fragment
    id: shopmall_sql_delete
    keyword: DELETE
    before_entity: FROM
    op_mappings: [{token: DELETE, op: DELETE}]
  - kind: sql_fragment
    id: shopmall_sql_select
    keyword: FROM
    op_mappings: [{token: FROM, op: SELECT}]
ddl_transform:
  ddl_naming_rule: as_is
  fallback_prefixes: []
call_wrappers:
  - wrapper: dispatch_call
    callee_from: first_arg_string
framework_runtime_functions:
  - LOG_DBG
  - SAFECPY
```

## Why these patterns

`(?:\s|"\s*)+`는 SQL 토큰 사이의 공백과 C 인접 문자열 경계만 처리한다. 원문을
합치거나 따옴표를 삭제하지 않는다. `[A-Za-z_][A-Za-z0-9_]*`는 `product_stock`처럼
밑줄이 포함된 전체 식별자를 그대로 추출한다.

정답:

```c
"UPDATE product_stock " "SET avail_qty = %d"
```

`entity=product_stock`이다. `product`로 자르거나 `productStock`으로 바꾸지 않는다.

오답:

```regex
(?P<entity>product|product_stock|product_price_hst)
```

왼쪽 대안 `product`가 먼저 성공해 뒤의 `_stock`, `_price_hst`를 남긴다. 실제 DDL 이름을
정규식 대안으로 나열하지 말고 전체 식별자를 추출한다.

런타임 이름은 미해결로 둔다:

```c
sprintf(g_sqlbuf, "UPDATE %s SET status_cd='20'", table_name);
```

`%s`를 DDL의 비슷한 이름으로 바꾸지 않는다. 이 프로젝트의 정적 테이블명에는 하이픈이
없다. 다른 프로젝트에서 quoted `"order-item"`처럼 하이픈이 실제 DDL 이름의 일부라면
그 프로젝트 Skill이 하이픈까지 전체 캡처해야 하며 `order_item`으로 정규화하면 안 된다.

## Do not drop project functions

`db_query`, `db_exec`, `db_next_seq`, `db_commit`, `db_rollback`, `db_encrypt`,
`db_decrypt`, `dispatch_call`, `log_event`, `write_audit`, `get_code_name`,
`SAFECPY_STR`는 프로젝트에 정의된 코드노드이므로 `framework_runtime_functions`에 넣지 않는다.
