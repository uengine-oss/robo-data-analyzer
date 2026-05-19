# SKILL: proframe-c-pfm-dbio

> ProFrame C 프레임워크의 ``mpfmdbio`` DB 접근 wrapper 패턴.

## 식별 단서

- 주 언어: C
- 핵심 wrapper: ``mpfmdbio("<SQL_KEY>", ...)`` 형식 호출
- SQL_KEY 형식: ``<TABLE>_<OPERATION_CODE>NNNN`` (예: ``USR_PROFILE_S001``)
- operation code: ``s``=SELECT, ``i``=INSERT, ``u``=UPDATE, ``d``=DELETE
- 매니페스트 단서: ``pfm_*.h`` / ``pfm_*.c`` / ``Makefile`` 의 ``-lpfmdbio``

## 정규식 패턴

```yaml
table_access_patterns:
  - id: proframe_mpfmdbio
    call_regex: 'mpfmdbio\s*\(\s*"(?P<entity>[A-Z0-9_]+)_(?P<op>[siud])\d{4}"'
    crud_map:
      s: READ
      i: WRITE
      u: WRITE
      d: WRITE
    entity_source: first_arg_string

ddl_transform:
  ddl_naming_rule: as_is        # entity 가 이미 DDL 테이블명 형식
  fallback_prefixes: []
```

## codebase_notes 예시

> ProFrame C 프로젝트. ``mpfmdbio`` wrapper 로 모든 DB 접근.
> SQL_KEY = ``<TABLE>_<s|i|u|d><번호>`` 형식. 첫 인자가 곧 SQL_KEY.
> 회사 표준이라 추가 변환 없이 entity = TABLE.

## 한계

- mpfmdbio 외 직접 SQL (``EXEC SQL`` 등) 은 별도 패턴 필요.
- 동적 SQL_KEY (런타임 조립) 는 unresolved 큐.
