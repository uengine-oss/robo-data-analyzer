# SKILL: mybatis-ibatis

> MyBatis / iBatis XML mapper 기반 DB 접근.

## 식별 단서

- 주 언어: Java / Kotlin
- XML mapper 파일: ``*Mapper.xml`` / ``sqlmap-*.xml``
- 매니페스트: ``mybatis-spring-boot-starter`` / ``ibatis-sqlmap``
- Mapper 인터페이스: ``@Mapper`` annotation 또는 ``SqlSession.selectOne(...)``
- 호출 패턴: ``sqlSession.selectList("namespace.id", ...)`` / Mapper 메서드 직접 호출

## 정규식 패턴

```yaml
table_access_patterns:
  - id: mybatis_sqlsession
    call_regex: 'sqlSession\.(?P<op>selectOne|selectList|insert|update|delete)\s*\(\s*"(?P<entity>[\w.]+)"'
    crud_map:
      selectOne: READ
      selectList: READ
      insert: WRITE
      update: WRITE
      delete: WRITE
    entity_source: first_arg_string

  - id: mybatis_mapper_call
    call_regex: '(\w+Mapper)\.(?P<op>select\w*|find\w*|insert\w*|update\w*|delete\w*|count\w*)\s*\('
    crud_map:
      select: READ
      find: READ
      insert: WRITE
      update: WRITE
      delete: WRITE
      count: READ
    entity_source: mapper_name_prefix

ddl_transform:
  ddl_naming_rule: camel_to_snake_upper
  fallback_prefixes: []
```

## XML mapper 별도 처리

XML mapper 파일도 함께 분석:
- ``<select id="findById"> SELECT ... FROM USERS ...``
- ``namespace.id`` → 함수 ↔ SQL statement REFER_TO 엣지
- SQL 본문의 ``FROM`` / ``JOIN`` 절에서 테이블 추출

## codebase_notes 예시

> MyBatis XML mapper 기반. Mapper 인터페이스 메서드 ↔ XML statement 1:1.
> namespace 가 Mapper FQN, id 가 메서드명.

## 한계

- 동적 SQL (``<if>``, ``<foreach>``) 의 조건부 테이블 접근은 추정.
