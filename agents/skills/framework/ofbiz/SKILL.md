# SKILL: ofbiz

> Apache OFBiz delegator / entitymodel 패턴.

## 식별 단서

- 주 언어: Java / Groovy
- 매니페스트: ``ofbiz-component.xml`` / ``entitymodel.xml``
- 핵심 wrapper: ``delegator.findOne(...)`` / ``delegator.findList(...)`` / ``delegator.store(...)``
- Entity 정의: ``entitymodel.xml`` 의 ``<entity entity-name="..." table-name="...">``

## 정규식 패턴

```yaml
table_access_patterns:
  - id: ofbiz_delegator
    call_regex: 'delegator\.(?P<op_token>findOne|findByPrimaryKey|findList|findAll|find|create|store|remove|removeAll)\s*\(\s*"(?P<entity>\w+)"'
    op_mappings:
      - {token: findOne,           op: SELECT}
      - {token: findByPrimaryKey,  op: SELECT}
      - {token: findList,          op: SELECT}
      - {token: findAll,           op: SELECT}
      - {token: find,              op: SELECT}
      - {token: create,            op: INSERT}
      - {token: store,             op: UPDATE}   # ofbiz store = update if PK exists
      - {token: remove,            op: DELETE}
      - {token: removeAll,         op: DELETE}

ddl_transform:
  ddl_naming_rule: camel_to_snake_upper      # entitymodel 의 entity-name → table-name 변환
  fallback_prefixes: []
```

## entitymodel.xml 별도 처리

OFBiz 는 entity 와 table 매핑이 ``entitymodel.xml`` 에 명시:
- ``<entity entity-name="OrderHeader" table-name="ORDER_HEADER">``
- 이 매핑을 우선 사용. ddl_transform 은 매핑 없는 entity 의 fallback.

## codebase_notes 예시

> Apache OFBiz 프로젝트. ``delegator`` 가 모든 DB 접근. entity-name 의 첫 인자가 핵심.
> entitymodel.xml 이 진실 — 거기서 entity → table 매핑 우선.
> **delegator 메서드명이 함수의 DB 작업 종류 결정:**
> ``findOne``/``findByPrimaryKey``/``findList``/``findAll``/``find`` = SELECT,
> ``create`` = INSERT, ``store`` = UPDATE, ``remove``/``removeAll`` = DELETE.
> 변환 없는 entity 는 camelCase → SNAKE_UPPER fallback.

## 한계

- 동적 entity-name (변수 치환) 은 unresolved 큐.
- EntityCondition 의 조건 절은 별도 분석.
