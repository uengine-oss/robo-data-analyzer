# SKILL: proframe-c-pfm-dbio

> ProFrame C 프레임워크의 ``mpfmdbio`` DB 접근 wrapper 패턴.

## 식별 단서

- 주 언어: C
- 핵심 wrapper: ``mpfmdbio("<SQL_KEY>", ...)`` 형식 호출
- SQL_KEY 형식: 아래 ## SQL_KEY 명명 컨벤션 참조 (두 변종 존재)
- operation code: ``s``=SELECT, ``i``=INSERT, ``u``=UPDATE, ``d``=DELETE
- 매니페스트 단서: ``pfm_*.h`` / ``pfm_*.c`` / ``Makefile`` 의 ``-lpfmdbio``

## SQL_KEY 명명 컨벤션

ProFrame SQL_KEY 는 두 형식이 존재한다:

1. **본 테이블 R/W** — ``<table>_<op><nnnn>``
   - 예: ``zpay_ap_rltm_auth_hst_i0002`` (테이블=``zpay_ap_rltm_auth_hst``, op=``i`` INSERT)
   - 예: ``zord_jncard_s0011`` (테이블=``zord_jncard``, op=``s`` SELECT)
   - 본 테이블의 일반 데이터 R/W 가 여기 해당.

2. **시퀀스 채번 wrapper** — ``<table>_s_<op><nnnn>``
   - 예: ``zpay_ap_auth_req_tmp_s_s0001`` (테이블=``zpay_ap_auth_req_tmp``, 시퀀스 채번)
   - 중간 ``_s`` 는 Oracle ``SEQUENCE`` 객체 호출 wrapper 표시.
   - 그 다음 INSERT (``<table>_i<nnnn>``) 의 PK 로 쓸 유일 번호 받아오는 SELECT.
   - 코드 단서: 헤더/구조체 주석에 "BANK SEQ", 에러 메시지에 "BANK SEQ 조회중 에러".

### 시퀀스 매핑 — 본 테이블 READ 로 단일화 (영역 결정)

시퀀스는 본 테이블이 아닌 별도 DB 객체이지만, **본 테이블의 READ 엣지로 단일화**해
그래프에 박는다.

- 이유: 시퀀스 채번은 그 다음 INSERT 와 한 묶음 행위. 그 함수가 그 테이블 작업한다는
  사실은 시퀀스만 봐도 보존됨.
- 별도 ``SEQUENCE`` 노드는 안 만든다 — 분석 가치 대비 영역 폭증 (DDL parser·온톨로지·
  다운스트림 동시 손봐야). 필요해지면 별건으로 추가.
- 시퀀스 wrapper 의 op 는 항상 ``s`` (SELECT) → 그래프엔 READS 엣지 (코드가 자동 매핑).

## 정규식 패턴

두 변종을 정규식 2개로 분리한다 — 의미가 다르므로 매칭 의도를 식별자(``id``)에 명시.
본 R/W 정규식은 ``lookbehind (?<!_s)`` 로 시퀀스 변종을 거부 (entity 가 ``_s`` 로
끝나는 잘못된 매치 차단). 시퀀스 정규식은 ``_s_`` 중간 토큰을 명시 요구.

```yaml
table_access_patterns:
  - id: proframe_mpfmdbio              # 본 테이블 R/W — 시퀀스 변종 거부 (lookbehind)
    call_regex: 'mpfmdbio\s*\(\s*"(?P<entity>[A-Za-z0-9_]+?)(?<!_s)_(?P<op_token>[siud])\d{4}"'
    op_mappings:
      - {token: s, op: SELECT}
      - {token: i, op: INSERT}
      - {token: u, op: UPDATE}
      - {token: d, op: DELETE}
    entity_source: first_arg_string

  - id: proframe_mpfmdbio_seq          # 시퀀스 채번 wrapper — 본 테이블 READ 로 단일화
    call_regex: 'mpfmdbio\s*\(\s*"(?P<entity>[A-Za-z0-9_]+?)_s_(?P<op_token>[siud])\d{4}"'
    op_mappings:
      - {token: s, op: SELECT}        # 시퀀스 호출은 항상 SELECT (코드가 READ 로 자동 매핑)
    entity_source: first_arg_string

ddl_transform:
  ddl_naming_rule: as_is        # entity 가 이미 DDL 테이블명 형식
  fallback_prefixes: []
```

## 호출 wrapper (인자가 실제 호출 대상)

ProFrame 의 동적 모듈 호출 — `mpfm_dlcall("<모듈명>", ...)`. 첫 인자 string 이
호출 대상 모듈. wrapper 자체는 framework_functions 에 들어가 드롭되지만 — 인자가
진짜 호출 타겟이므로 `call_classifier` 가 라인 본문에서 인자 추출 후 호출 그래프에
정합한다 (별도 step 없음 — Calls 엣지 단일 영역).

```yaml
call_wrappers:
  - wrapper: mpfm_dlcall
    callee_from: first_arg_string
```

`callee_from` = 인자 추출 위치 (`first_arg_string` = 첫 인자 큰따옴표 문자열).

인자가 분석 대상 코드의 module_id 와 일치하면 그 모듈 entry 함수로 internal,
일치 안 하면 인자 이름 그대로 external.

## 프레임워크 함수 (호출 분류 시 드롭)

ProFrame 의 런타임·매크로 함수. 업무 로직이 아니므로 호출 그래프에서 제외한다
(`ScanResult.framework_functions` 에 담음). **각 항목은 glob 패턴** —
`PRINT_*` 같이 와일드카드로 시리즈 전체 매칭 가능.

- 예외/흐름 매크로: `PFM_TRY` `PFM_TRYNJ` `PFM_CATCH` `PFM_MSGE` `PFM_ERRC` `PFM_ERRS` `PFM_DBG`
- DB IO 런타임: `mpfmdbio` `mpfmdbio_errno` `mpfmdbio_errstr` `mpfmdbio_reccnt` `mpfmdbio_free_blob` `mpfmdbio_alloc_blob` `mpfmdbio_write_blob`
- 유틸: `mpfm_isspnull` `mpfm_isnull` `mpfm_trim` `mpfm_dlcall` `mpfm_get_date_time` `mpfm_get_date_utime`
- 디버그 PRINT 매크로 (glob 시리즈): `PRINT_*` — `PRINT_<TABLE>_<OP>_IN_T` / `_OUT_T`
  계열. 자동 생성 디버그 매크로이고 비즈니스 호출이 아니므로 드롭.

> `mpfmdbio` 는 위 정규식(table_access_patterns)의 앵커이기도 하다 — 테이블 R/W 는
> 정규식이 잡고, 호출 그래프에서는 프레임워크 함수로 드롭된다 (서로 다른 단계).

## codebase_notes 예시

> ProFrame C 프로젝트. ``mpfmdbio`` wrapper 로 모든 DB 접근.
> SQL_KEY 두 형식 — 본 R/W ``<table>_<op><nnnn>``, 시퀀스 채번 ``<table>_s_<op><nnnn>``.
> 첫 인자가 곧 SQL_KEY. 회사 표준이라 추가 변환 없이 entity = TABLE.
> **SQL_KEY 의 ``<op>``: s=SELECT, i=INSERT, u=UPDATE, d=DELETE — 함수의 DB 작업 종류 결정**
> (예: ``..._s0024`` = SELECT, ``..._i0002`` = INSERT). 코드 주석이 op 와 어긋나면 SQL_KEY 가 진실.
> 시퀀스 채번은 본 테이블 READ 로 단일화 (별도 SEQUENCE 노드 없음).

## 한계

- mpfmdbio 외 직접 SQL (``EXEC SQL`` 등) 은 별도 패턴 필요.
- 동적 SQL_KEY (런타임 조립) 는 unresolved 큐.
- 테이블 이름이 ``_s`` 로 끝나는 경우는 본 R/W 정규식이 거부 — 잔여 루프가 처리
  (현 7테이블 DDL 엔 해당 케이스 없음).
