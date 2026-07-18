# SKILL: mybatis-ibatis

> MyBatis / iBatis XML mapper 기반 DB 접근.

## 식별 단서

- 주 언어: Java / Kotlin
- XML mapper 파일: ``*Mapper.xml`` / ``sqlmap-*.xml``
- 매니페스트: ``mybatis-spring-boot-starter`` / ``ibatis-sqlmap``
- Mapper 인터페이스: ``@Mapper`` annotation 또는 ``SqlSession.selectOne(...)``
- 호출 패턴: ``sqlSession.selectList("namespace.id", ...)`` / Mapper 메서드 직접 호출

## 정규식 패턴

정규식의 `<op_token>` 그룹은 메서드명 **prefix** 만 잡는다 (뒤에 `\w*` 로 suffix 흘림).
이렇게 해야 매치된 토큰이 `op_mappings` 의 token 과 정확히 일치한다.

```yaml
table_access_patterns:
  - id: mybatis_sqlsession
    call_regex: 'sqlSession\.(?P<op_token>selectOne|selectList|insert|update|delete)\s*\(\s*"(?P<entity>[\w.]+)"'
    op_mappings:
      - {token: selectOne,  op: SELECT}
      - {token: selectList, op: SELECT}
      - {token: insert,     op: INSERT}
      - {token: update,     op: UPDATE}
      - {token: delete,     op: DELETE}

  - id: mybatis_mapper_call
    call_regex: '(\w+Mapper)\.(?P<op_token>select|find|insert|update|delete|count)\w*\s*\('
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
