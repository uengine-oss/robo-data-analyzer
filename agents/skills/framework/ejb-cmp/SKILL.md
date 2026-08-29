# SKILL: ejb-cmp

> EJB CMP (Container-Managed Persistence) 패턴 — 레거시 J2EE.

## 식별 단서

- 주 언어: Java
- 매니페스트: ``ejb-jar.xml`` / ``weblogic-cmp-rdbms-jar.xml``
- 빈 정의: ``<entity><persistence-type>Container</persistence-type></entity>``
- 호출 패턴: ``EntityHome.findByPrimaryKey(...)`` / ``create(...)`` / ``remove()``
- getter/setter 가 DB 컬럼 직접 매핑

## 접근 규칙 (DSL — spec 108)

`op_prefixes` 는 메서드명 **prefix** 만 (suffix 는 코드가 흘림). entity 는 리시버
이름에서 (`orderHome` → `order` → ddl_transform).
※ 구판 raw 정규식은 `<entity>` group 누락으로 링킹 불가(죽은 규칙)였다 — 이사에서 교정.
※ 구판의 ``ejb_cmp_setter``(``.setXxx(`` → UPDATE) 규칙은 **제거**했다: 리시버가 엔티티
인스턴스 변수명이라 정적으로 테이블을 알 수 없어(entity 원천 부재) 어떤 표현으로도
엣지를 만들 수 없는 원천 무효 규칙이었다. setter 유래 UPDATE 는 unresolved 큐가 담당.

```yaml
table_access_rules:
  - kind: method_call
    id: ejb_cmp_home
    receiver_suffix: Home
    entity_from: receiver
    op_prefixes: [find, create, remove]
    op_mappings:
      - {token: find,   op: SELECT}      # findByPrimaryKey / findByXxx / findAll 모두 prefix=find
      - {token: create, op: INSERT}
      - {token: remove, op: DELETE}

ddl_transform:
  ddl_naming_rule: camel_to_snake_upper
  fallback_prefixes: []
```

## 접근 의미

> 레거시 EJB CMP 프로젝트. Entity Bean 의 ``Home.<method>`` 가 DB 직접 매핑.
> **Home 메서드명·setter 호출이 함수의 DB 작업 종류 결정:**
> ``find``/``findBy...`` = SELECT, ``create`` = INSERT, ``set<Field>(...)`` = UPDATE,
> ``remove`` = DELETE.
> 회사 표준 ``ejb-jar.xml`` 에 ``<cmp-field>`` 가 정확한 컬럼 매핑.

## 한계

- BMP (Bean-Managed Persistence) 는 별도 SKILL 또는 unresolved 큐.
- ejbSelect (커스텀 EJBQL) 은 별도 분석.
