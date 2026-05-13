---
name: proframe-c-pfm-dbio
description: >
  ProFrame C 코드 (`.c`) + `pfmdbio.h` include + `mpfmdbio()` 호출 패턴일 때 사용.
  본문을 그대로 final answer 로 반환.
---

# ProFrame C — PFM 프레임워크 + mpfmdbio 패턴

## 1. 프레임워크 제외 심볼

prefix `PFM_*` / `mpfm_*` / `pfm_*` / `PRINT_*` 는 프레임워크 — 함수 호출 아님.

| 그룹 | 심볼 |
| --- | --- |
| 래퍼 매크로 | `PFM_TRY`, `PFM_TRYNJ`, `PFM_CATCH` |
| 에러/로깅 | `PFM_ERRC`, `PFM_ERRS`, `PFM_MSGE`, `PFM_DBG`, `PRINT_*` |
| 반환 상수 | `RC_OK`, `RC_NRM`, `RC_NFD`, `RC_ERR` |
| DB 메타 헬퍼 | `mpfmdbio_errno`, `mpfmdbio_errstr`, `mpfmdbio_reccnt` |

## 2. DB 접근 패턴

`mpfmdbio(dbio_id_literal, &in, &out)` — 테이블 CRUD 전용. 함수 호출 아님.

**Identifier Rule**: `<table>_<crud><4digit>` → trailing suffix strip = 테이블명.
예: `"zpay_ap_rltm_auth_hst_u0001"` → 테이블 `zpay_ap_rltm_auth_hst`, CRUD `u`.

**Sequence 구분**: 가운데 `_s_` 삽입 + input `NULL` + output `.nextval` → Table 아님, 제외.
예: `zpay_ap_auth_req_tmp_s_s0001` → sequence. base table = `zpay_ap_auth_req_tmp`.

| suffix | op | relation |
| --- | --- | --- |
| `_s####` | SELECT | READS |
| `_i####` | INSERT | WRITES |
| `_u####` | UPDATE | WRITES |
| `_d####` | DELETE | WRITES |

**결과**: `RC_NRM` = 성공, `RC_NFD` = no data, 그 외 = 에러.

## 3. 동적 모듈 호출 (cross-module dispatch)

`mpfm_dlcall(module_literal, &io)` — 1번째 인자가 **타깃 모듈명**.
예: `mpfm_dlcall("zapamcom10040", &io)` → 호출 대상 = `zapamcom10040`.

## 4. 함수 호출 패턴

**같은 파일 내 직접 호출** — `PFM_TRY`/`PFM_TRYNJ` 래퍼로 감싸서 호출.
예: `PFM_TRY( b000_main_proc(itf) )` → 호출 대상 = `b000_main_proc`.

**크로스 모듈 동적 호출** — `mpfm_dlcall`로 모듈명 string literal 전달.
예: `mpfm_dlcall("zapamcom10040", &io)` → 호출 대상 = `zapamcom10040`.

이 두 패턴만 함수 호출임. 나머지(`mpfmdbio`, `PFM_ERRC`, `mpfm_*` 유틸 등)는 함수 호출 아님.
