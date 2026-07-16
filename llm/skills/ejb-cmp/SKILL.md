# SKILL: ejb-cmp

> EJB CMP (Container-Managed Persistence) 패턴 — 레거시 J2EE.

## 식별 단서

- 주 언어: Java
- 매니페스트: ``ejb-jar.xml`` / ``weblogic-cmp-rdbms-jar.xml``
- 빈 정의: ``<entity><persistence-type>Container</persistence-type></entity>``
- 호출 패턴: ``EntityHome.findByPrimaryKey(...)`` / ``create(...)`` / ``remove()``
- getter/setter 가 DB 컬럼 직접 매핑

## 정규식 패턴

정규식의 `<op_token>` 그룹은 메서드명 **prefix** 만 잡는다 (뒤에 `\w*` 로 suffix 흘림).
이렇게 해야 매치된 토큰이 `op_mappings` 의 token 과 정확히 일치한다.

```yaml
table_access_patterns:
  - id: ejb_cmp_home
    call_regex: '(\w+Home)\.(?P<op_token>find|create|remove)\w*\s*\('
    op_mappings:
      - {token: find,   op: SELECT}      # findByPrimaryKey / findByXxx / findAll 모두 prefix=find
      - {token: create, op: INSERT}
      - {token: remove, op: DELETE}
    entity_source: home_name_prefix         # "OrderHome" → "Order"

  - id: ejb_cmp_setter
    call_regex: '\.(?P<op_token>set)[A-Z]\w+\s*\('
    op_mappings:
      - {token: set, op: UPDATE}            # setter 호출은 CMP UPDATE 트리거
    entity_source: caller_class_name

ddl_transform:
  ddl_naming_rule: camel_to_snake_upper
  fallback_prefixes: []
```

## codebase_notes 예시

> 레거시 EJB CMP 프로젝트. Entity Bean 의 ``Home.<method>`` 가 DB 직접 매핑.
> **Home 메서드명·setter 호출이 함수의 DB 작업 종류 결정:**
> ``find``/``findBy...`` = SELECT, ``create`` = INSERT, ``set<Field>(...)`` = UPDATE,
> ``remove`` = DELETE.
> 회사 표준 ``ejb-jar.xml`` 에 ``<cmp-field>`` 가 정확한 컬럼 매핑.

## 한계

- BMP (Bean-Managed Persistence) 는 별도 SKILL 또는 unresolved 큐.
- ejbSelect (커스텀 EJBQL) 은 별도 분석.
