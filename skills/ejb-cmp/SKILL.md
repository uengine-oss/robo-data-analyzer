# SKILL: ejb-cmp

> EJB CMP (Container-Managed Persistence) 패턴 — 레거시 J2EE.

## 식별 단서

- 주 언어: Java
- 매니페스트: ``ejb-jar.xml`` / ``weblogic-cmp-rdbms-jar.xml``
- 빈 정의: ``<entity><persistence-type>Container</persistence-type></entity>``
- 호출 패턴: ``EntityHome.findByPrimaryKey(...)`` / ``create(...)`` / ``remove()``
- getter/setter 가 DB 컬럼 직접 매핑

## 정규식 패턴

```yaml
table_access_patterns:
  - id: ejb_cmp_home
    call_regex: '(\w+Home)\.(?P<op>findBy\w*|find\w*|create|remove)\s*\('
    crud_mappings:
      - {op: findBy, crud: READ}
      - {op: find, crud: READ}
      - {op: create, crud: WRITE}
      - {op: remove, crud: WRITE}
    entity_source: home_name_prefix         # "OrderHome" → "Order"

  - id: ejb_cmp_setter
    call_regex: '\.set([A-Z]\w+)\s*\('
    crud_mappings:
      - {op: default, crud: WRITE}
    entity_source: caller_class_name

ddl_transform:
  ddl_naming_rule: camel_to_snake_upper
  fallback_prefixes: []
```

## codebase_notes 예시

> 레거시 EJB CMP 프로젝트. Entity Bean 의 ``Home.findByPrimaryKey``,
> ``create``, ``remove`` 가 DB 직접 매핑. setter 호출이 UPDATE 트리거.
> 회사 표준 ``ejb-jar.xml`` 에 ``<cmp-field>`` 가 정확한 컬럼 매핑.

## 한계

- BMP (Bean-Managed Persistence) 는 별도 SKILL 또는 unresolved 큐.
- ejbSelect (커스텀 EJBQL) 은 별도 분석.
