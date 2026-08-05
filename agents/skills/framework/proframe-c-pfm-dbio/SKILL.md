# SKILL: proframe-c-pfm-dbio

> ProFrame C 프레임워크의 ``mpfmdbio`` DB 접근 wrapper 패턴.

## 식별 단서

- 주 언어: C
- 핵심 wrapper: ``mpfmdbio("<SQL_KEY>", ...)`` 형식 호출
- SQL_KEY 형식: 아래 ## SQL_KEY 명명 컨벤션 참조 (두 변종 존재)
- operation code: ``s``=SELECT, ``i``=INSERT, ``u``=UPDATE, ``d``=DELETE
- 매니페스트 단서: ``pfm_*.h`` / ``pfm_*.c`` / ``Makefile`` 의 ``-lpfmdbio``

## SQL_KEY 명명 컨벤션

ProFrame SQL_KEY 에서 TABLE R/W로 해석할 수 있는 형식과 다른 DB 객체 형식을 구분한다:

1. **본 테이블 R/W** — ``<table>_<op><nnnn>``
   - 예: ``zpay_ap_rltm_auth_hst_i0002`` (테이블=``zpay_ap_rltm_auth_hst``, op=``i`` INSERT)
   - 예: ``zord_jncard_s0011`` (테이블=``zord_jncard``, op=``s`` SELECT)
   - 본 테이블의 일반 데이터 R/W 가 여기 해당.

2. **시퀀스 채번 wrapper** — ``<sequence-key>_s_<op><nnnn>``
   - 예: ``zpay_ap_auth_req_tmp_s_s0001`` (시퀀스 DB 객체 채번)
   - 중간 ``_s`` 는 Oracle ``SEQUENCE`` 객체 호출 wrapper 표시.
   - 그 다음 INSERT (``<table>_i<nnnn>``) 의 PK 로 쓸 유일 번호 받아오는 SELECT.
   - 코드 단서: 헤더/구조체 주석에 "BANK SEQ", 에러 메시지에 "BANK SEQ 조회중 에러".

### 시퀀스는 TABLE 관계에서 제외

시퀀스는 TABLE이 아닌 별도 DB 객체다. 현재 그래프가 ``SEQUENCE`` 노드를 모델링하지 않으므로
이 key에서 TABLE READ/WRITE 엣지를 만들지 않는다. 같은 함수의 실제 TABLE INSERT/UPDATE는
본 R/W 형식의 별도 key가 근거다. 이름이 우연히 DDL TABLE과 같아도 시퀀스 key를 TABLE로
투영하지 않는다.

## 접근 규칙 (DSL — spec 108)

TABLE R/W 규칙은 ``entity_not_ending: "_s"`` 로 시퀀스 변종을 거부한다(entity가 ``_s``로
끝나는 잘못된 매치 차단). 시퀀스는 TABLE 접근 규칙에 넣지 않는다. 정규식은 코드가
규칙에서 기계 생성한다 — 직접 쓰지 말 것.

```yaml
table_access_rules:
  - kind: wrapper_key
    id: proframe_mpfmdbio              # 본 테이블 R/W — 시퀀스 변종 거부
    wrapper: mpfmdbio
    separator: "_"
    op_tokens: [s, i, u, d]
    suffix_digits: 4
    entity_not_ending: "_s"
    op_mappings:
      - {token: s, op: SELECT}
      - {token: i, op: INSERT}
      - {token: u, op: UPDATE}
      - {token: d, op: DELETE}
table_access_exclusion_rules:
  - kind: wrapper_key
    id: proframe_mpfmdbio_sequence
    wrapper: mpfmdbio
    entity_suffix: "_s"
    separator: "_"
    op_tokens: [s, i, u, d]
    suffix_digits: 4
ddl_transform:
  ddl_naming_rule: as_is        # entity 가 이미 DDL 테이블명 형식
  fallback_prefixes: []

# 분기표 [handler] 행 — 예외 핸들러 진입 라벨 (spec 116). try/catch 대신 쓰는 매크로.
handler_markers:
  - PFM_CATCH

# 값·객체 추적용 API 계약 (spec 116 2단계) — 문서로 보증되는 사실만, 추측 금지.
# arg_roles 의 키는 0-based 인자 위치. 채점기가 via_call·효과 대조에 사용한다.
api_contracts:
  - name: mpfmdbio
    arg_roles:
      0: sql_key            # 첫 인자 = SQL_KEY (테이블·op 결정 — 위 명명 컨벤션)
    success_value: RC_NRM   # PFM_TRY(expr) 가 RC_NRM 비교로 성공을 판정
  - name: mpfm_dlcall
    arg_roles:
      0: callee_module      # 첫 인자 문자열 = 실제 호출 대상 모듈

framework_runtime_functions:
  - PFM_TRY
  - PFM_TRYNJ
  - PFM_CATCH
  - PFM_MSGE
  - PFM_ERRC
  - PFM_ERRS
  - PFM_DBG
  - mpfmdbio
  - mpfmdbio_errno
  - mpfmdbio_errstr
  - mpfmdbio_reccnt
  - mpfmdbio_free_blob
  - mpfmdbio_alloc_blob
  - mpfmdbio_write_blob
  - mpfm_isspnull
  - mpfm_isnull
  - mpfm_trim
  - mpfm_dlcall
  - mpfm_get_date_time
  - mpfm_get_date_utime
  - PRINT_*

codebase_notes: >-
  ProFrame C 프로젝트. mpfmdbio wrapper의 첫 인자는 SQL_KEY다. TABLE R/W key는
  <table>_<op><nnnn>이고 시퀀스 객체 key는 <sequence-key>_s_<op><nnnn>다.
  SQL_KEY <op>의 s/i/u/d가 SELECT/INSERT/UPDATE/DELETE를 결정하고 코드 주석과
  어긋나면 SQL_KEY가 진실이다. 시퀀스 객체 key는 TABLE R/W 관계로 만들지 않는다.
  mpfm_dlcall의 첫 문자열 인자는 호출 대상 모듈이다. PFM_TRY(expr)는 expr 결과가
  RC_NRM이 아니면 현재 함수의 PFM_CATCH로 제어를 넘기며, 정확한 최종 반환·복구
  효과는 활성 PFM_CATCH 본문으로 확정한다.
```

## 호출 wrapper (인자가 실제 호출 대상)

ProFrame 의 동적 모듈 호출 — `mpfm_dlcall("<모듈명>", ...)`. 첫 인자 string 이
호출 대상 모듈. wrapper 자체는 framework_runtime_functions 에 들어가 드롭되지만 — 인자가
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
(`ScanResult.framework_runtime_functions` 에 담음). **각 항목은 glob 패턴** —
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
> SQL_KEY 두 형식 — 본 R/W ``<table>_<op><nnnn>``, 시퀀스 객체
> ``<sequence-key>_s_<op><nnnn>``.
> 첫 인자가 곧 SQL_KEY. 회사 표준이라 추가 변환 없이 entity = TABLE.
> **SQL_KEY 의 ``<op>``: s=SELECT, i=INSERT, u=UPDATE, d=DELETE — 함수의 DB 작업 종류 결정**
> (예: ``..._s0024`` = SELECT, ``..._i0002`` = INSERT). 코드 주석이 op 와 어긋나면 SQL_KEY 가 진실.
> 시퀀스 객체 key는 TABLE R/W 관계로 만들지 않는다(현재 별도 SEQUENCE 노드는 없음).
> ``PFM_TRY(expr)``는 `expr` 결과가 ``RC_NRM``이 아니면 현재 함수의 ``PFM_CATCH``로
> 제어를 넘긴다. 정확한 최종 반환·복구 효과는 해당 함수의 활성 ``PFM_CATCH`` 본문으로
> 확정하며, 호출 실패 뒤 정상 흐름이 계속된다고 해석하지 않는다.

## 한계

- mpfmdbio 외 직접 SQL (``EXEC SQL`` 등) 은 별도 패턴 필요.
- 동적 SQL_KEY (런타임 조립) 는 unresolved 큐.
- 테이블 이름이 ``_s`` 로 끝나는 경우는 본 R/W 정규식이 거부 — 잔여 루프가 처리
  (현 7테이블 DDL 엔 해당 케이스 없음).
