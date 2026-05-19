# SKILL: <skill-name>

> 회사별 / 프레임워크별 함수 ↔ 테이블 접근 패턴 정의 템플릿.
> Profile Agent 가 `list_skills()` / `read_skill(name)` 로 발견 + 본문 인용.

## 식별 단서

이 SKILL 이 매칭되는 조건 (Profile Agent 가 Phase A 에서 검증):
- 주 언어: <예: Java>
- 핵심 라이브러리 / wrapper: <예: ofbiz delegator, mpfmdbio>
- 명명 컨벤션: <예: camelCase 엔티티 ↔ snake_case_upper DDL>
- 매니페스트 단서: <예: pom.xml 에 ofbiz dependency>
- 호출 패턴 예: <예: `delegator.findOne(entityName, ...)`>

## 정규식 패턴

```yaml
table_access_patterns:
  - id: <skill_name>_<pattern_name>
    call_regex: '<regex with named groups <op> and <entity>>'
    crud_map:
      <op_value>: READ      # 또는 WRITE
      ...
    entity_source: first_arg_string   # 또는 method_name_suffix 등

ddl_transform:
  ddl_naming_rule: <as_is | camel_to_snake_upper | snake_to_camel>
  fallback_prefixes:
    - <T_>
    - <TB_>
```

## codebase_notes 예시

Profile Agent 가 ProfileResult.codebase_notes 에 박을 한 단락 예:

> 이 프로젝트는 <wrapper> 패턴으로 DB 접근. 엔티티 이름은 camelCase 로 코드에 등장하지만 DDL 은 snake_case_upper. 회사 표준 prefix T_ 가 모든 테이블에 붙음. <wrapper>.<method>(entityName, ...) 형식 호출 87건 확인.

## 한계

- Profile Agent 가 자기 말로 풀어쓰면 안 됨 — 이 SKILL.md 본문 그대로 인용.
- 회사별 DAO 박스가 다양하면 회사 SKILL 추가 작성 필요.
