---
name: proframe-c-pfm-dbio
description: >
  ProFrame C 코드 (`.c`) + `pfmdbio.h` include + `mpfmdbio()` 호출 패턴일 때 사용.
  함수명 prefix 는 `b###_dbio_*` 외에도 `b###_check_*` 같이 다양한 패턴이 공존 가능
  — 단일 prefix 단정 금지. 이 스킬 본문을 그대로 final answer 로 반환하면 됨.
---

# ProFrame C — PFM 프레임워크 + mpfmdbio 패턴

## 프레임워크 패턴

`PFM_TRY` / `PFM_TRYNJ` / `PFM_CATCH` 매크로로 호출 감싸고 비-`RC_OK` 반환은 즉시 propagate.
에러 코드 `RC_OK` / `RC_NRM` / `RC_NFD` / `RC_ERR`. 로깅 `PFM_ERRC` / `PFM_ERRS` / `PFM_MSGE` /
`PRINT_*`. 표준 C 라이브러리 (`memset` / `strncpy` / `strncmp`) 직접 사용.

## 심볼 역할 분류

| 심볼                                 | 역할       | 처리    |
| ------------------------------------ | ---------- | ------- |
| `PFM_TRY`, `PFM_TRYNJ`, `PFM_CATCH` | 래퍼       | unwrap  |
| `PFM_ERRC`, `PFM_ERRS`, `PFM_MSGE`  | 에러 반환  | exclude |
| `PRINT_*`, `PFM_DBG`                | 로깅       | exclude |
| `RC_OK`, `RC_NRM`, `RC_NFD`, `RC_ERR` | 상수 (반환 코드) | exclude |
| `mpfmdbio`                          | DB 접근    | extract |
| `memset`, `strncpy`, `strncmp`      | stdlib     | exclude |

## DB 접근 패턴

### 접근 메커니즘

`mpfmdbio(table_id_literal, &in_struct, &out_struct)` — 1번째 인자는 항상 문자열 리터럴
(상수/define 우회 없음).

### 식별자 규칙 (Identifier Rule)

리터럴 형태: `<table_name>_<crud_letter><4-digit>`. **테이블명 추출하려면 trailing
`_<crud><4-digit>` suffix strip**.

| Raw 리터럴                             | 정규화된 테이블명                | CRUD | 접근   |
| -------------------------------------- | -------------------------------- | ---- | ------ |
| `"zngm_comm_cd_dtl_s0024"`             | `zngm_comm_cd_dtl`               | `s`  | READS  |
| `"zngm_comm_cd_dtl_s0058"`             | `zngm_comm_cd_dtl`               | `s`  | READS  |
| `"zpay_ap_rltm_auth_hst_i0002"`        | `zpay_ap_rltm_auth_hst`          | `i`  | WRITES |
| `"zpay_ap_rltm_auth_hst_u0001"`        | `zpay_ap_rltm_auth_hst`          | `u`  | WRITES |
| `"zpay_ap_rltm_auth_hst_u0005"`        | `zpay_ap_rltm_auth_hst`          | `u`  | WRITES |

`_d####` (DELETE) 도 동일 규칙 — 이 파일에는 사용 안 됐지만 다른 파일에서 나타날 수 있음.

여러 raw 리터럴이 같은 테이블명으로 정규화되는 게 정상 — 각 호출마다 별도 access edge 등록.

### 명명 규칙 (CRUD)

`_s####` → **READS** (SELECT)
`_i####` / `_u####` / `_d####` → **WRITES** (INSERT / UPDATE / DELETE)

### 결과/에러

`RC_NRM` (또는 `RC_OK`) = 성공. `RC_NFD` = 데이터 없음. 그 외 = 에러
(`mpfmdbio_errno()` / `mpfmdbio_errstr()` 로 상세).

## 함수 명명 패턴 (참고용)

DB 래퍼 함수의 이름 prefix 가 다양함 — `b###_dbio_*`, `b###_check_*` 등 같은 코드베이스에 공존 가능. **단일 prefix 단정 금지**. 다운스트림 검색은 함수 이름 패턴이 아니라 `mpfmdbio` 호출 본문 기준.

## 다운스트림 linker 권장 검색

1. `bundle_search("mpfmdbio", kind="text")` — 호출처 **모두** 수집. 함수 이름 prefix 와 무관하게 호출 본문이 있는 모든 chunk 검토.
2. 각 호출 본문에서 1번째 인자 리터럴 추출 → suffix strip → access 결정 → edge 등록 + 컬럼 동시.
3. 등록한 정규화 테이블명으로 **reverse-search** — 간접 접근(변수경유/helper경유) 함수 추가 발견.

🚫 `bundle_search("dbio_", kind="symbol")` 처럼 함수 이름 prefix 만 검색해서 R/W 등록 종료 — `b###_check_*` 같은 다른 prefix 함수 누락 위험.
