# SKILL: mybatis-ibatis

> MyBatis / iBatis XML mapper 기반 DB 접근.

## 식별 단서

- 주 언어: Java / Kotlin
- XML mapper 파일: ``*Mapper.xml`` / ``sqlmap-*.xml``
- 매니페스트: ``mybatis-spring-boot-starter`` / ``ibatis-sqlmap``
- Mapper 인터페이스: ``@Mapper`` annotation 또는 ``SqlSession.selectOne(...)``
- 호출 패턴: ``sqlSession.selectList("namespace.id", ...)`` / Mapper 메서드 직접 호출

## 접근 규칙 (DSL — spec 108)

`op_prefixes` 는 메서드명 **prefix** 만 (suffix 는 코드가 흘림). sqlSession 규칙의
entity 는 첫 문자열 인자(namespace.id), Mapper 규칙의 entity 는 리시버 이름
(`orderMapper` → `order`). ※ 구판 mapper raw 정규식은 `<entity>` group 누락으로
링킹 불가(죽은 규칙)였다 — 이사에서 교정.

```yaml
table_access_rules:
  - kind: method_call
    id: mybatis_sqlsession
    receiver_name: sqlSession
    entity_from: first_arg_string
    first_arg_charset: '[\w.]'
    op_prefixes: [selectOne, selectList, insert, update, delete]
    op_mappings:
      - {token: selectOne,  op: SELECT}
      - {token: selectList, op: SELECT}
      - {token: insert,     op: INSERT}
      - {token: update,     op: UPDATE}
      - {token: delete,     op: DELETE}

  - kind: method_call
    id: mybatis_mapper_call
    receiver_suffix: Mapper
    entity_from: receiver
    op_prefixes: [select, find, insert, update, delete, count]
    op_mappings:
      - {token: select, op: SELECT}
      - {token: find,   op: SELECT}
      - {token: insert, op: INSERT}
      - {token: update, op: UPDATE}
      - {token: delete, op: DELETE}
      - {token: count,  op: SELECT}

ddl_transform:
  ddl_naming_rule: camel_to_snake_upper
  fallback_prefixes: []
```

## codebase_notes 예시

> MyBatis XML mapper 기반. Mapper 인터페이스 메서드 ↔ XML statement 1:1.
> namespace 가 Mapper FQN, id 가 메서드명.
> **메서드명·sqlSession 호출명 prefix 가 함수의 DB 작업 종류 결정:**
> ``select``/``selectOne``/``selectList``/``find``/``count`` = SELECT, ``insert`` = INSERT,
> ``update`` = UPDATE, ``delete`` = DELETE.

## 한계

- 동적 SQL (``<if>``, ``<foreach>``) 의 조건부 테이블 접근은 추정.
- XML mapper 의 SQL 본문 직접 분석은 미지원 — Mapper 인터페이스 메서드를 통한 매핑만.
