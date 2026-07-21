# SKILL: jpa-standard

> JPA (Java Persistence API) 표준 Entity / Repository 패턴.

## 식별 단서

- 주 언어: Java / Kotlin
- 매니페스트: `pom.xml` 의 `spring-data-jpa` 또는 `hibernate-core`
- annotation: `@Entity` / `@Table` / `@Column` / `@Id`
- Repository: `JpaRepository<T, ID>` / `CrudRepository`
- 호출 패턴: `repository.findBy*(...)` / `save(...)` / `delete(...)`

## 정규식 패턴

정규식의 `<op_token>` 그룹은 메서드명 **prefix** 만 잡는다 (뒤에 `\w*` 로 suffix 흘림).
이렇게 해야 매치된 토큰이 `op_mappings` 의 token 과 정확히 일치한다.

```yaml
table_access_patterns:
  - id: jpa_repository_call
    call_regex: '(\w+Repository)\.(?P<op_token>find|save|delete|update|exists|count)\w*\s*\('
    op_mappings:
      - {token: find,   op: SELECT}    # findOne / findAll / findByXxx 모두 prefix=find
      - {token: save,   op: INSERT}    # JPA save 는 새 row 또는 merge — 의미상 INSERT
      - {token: delete, op: DELETE}
      - {token: update, op: UPDATE}
      - {token: exists, op: SELECT}
      - {token: count,  op: SELECT}

ddl_transform:
  ddl_naming_rule: camel_to_snake_upper      # User → USER, OrderItem → ORDER_ITEM
  fallback_prefixes: ["T_", "TB_"]           # 회사 prefix 가능성
```

## codebase_notes 예시

> Spring Data JPA 표준 프로젝트. ``@Entity`` 클래스가 DDL 테이블 1:1 대응.
> **Repository 메서드명 prefix 가 함수의 DB 작업 종류 결정:**
> ``find``/``exists``/``count`` = SELECT, ``save`` = INSERT, ``update`` = UPDATE, ``delete`` = DELETE.
> Entity 클래스명 = 테이블명 (camelCase → SNAKE_UPPER 변환).

## 한계

- Custom Query (``@Query`` annotation) 는 별도 SQL 분석 필요.
- Native Query (``nativeQuery=true``) 도 unresolved 큐.
