---
name: proframe-c-pfm-dbio
description: >
  ProFrame C 코드 (`.c`) + `pfmdbio.h` include + `mpfmdbio()` 호출 + 함수명 `b###_dbio_*_<crud>####`
  형태일 때 사용. 이 스킬 본문을 그대로 final answer 로 반환하면 됨.
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

## 다운스트림 linker 권장 검색

1. `bundle_search("mpfmdbio", kind="text")` — 호출처 모두 수집
2. 각 호출 본문에서 1번째 인자 리터럴 추출 → suffix strip → access 결정 → edge 등록 + 컬럼 동시
3. 등록한 정규화 테이블명으로 **reverse-search** — 간접 접근(변수경유/helper경유) 함수 추가 발견
