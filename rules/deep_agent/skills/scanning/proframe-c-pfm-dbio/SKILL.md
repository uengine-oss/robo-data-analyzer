---
name: proframe-c-pfm-dbio
description: >
  ProFrame C 코드 (`.c`) + `pfmdbio.h` include + `mpfmdbio()` 호출 패턴일 때 사용.
  본문을 그대로 final answer 로 반환.
---

# ProFrame C — PFM 프레임워크 + mpfmdbio 패턴

## 1. 프레임워크 패턴

`PFM_TRY` / `PFM_TRYNJ` / `PFM_CATCH` 로 호출 감싸고 비-`RC_OK` 반환은 즉시 propagate. 에러 코드 `RC_OK` / `RC_NRM` / `RC_NFD` / `RC_ERR`. 로깅 `PFM_ERRC` / `PFM_ERRS` / `PFM_MSGE` / `PRINT_*`. 표준 C 라이브러리 (`memset` / `strncpy` / `strncmp`) 직접 사용.

## 2. 심볼 역할 분류

### 2.1 언어 표준 라이브러리

`memset` / `strncpy` / `strncmp` / `printf` / `malloc` 등 ANSI C 표준. 정적 stdlib 목록 보유.

### 2.2 ProFrame 프레임워크 라이브러리

**헤더 (angle bracket — system 헤더):**

- `<pfmdbio.h>`
- `<pfmcom.h>`
- `<pfmutil.h>`

**Prefix 패턴:**

- `mpfm_*`
- `PFM_*`
- `pfm_*`

**Exact 심볼 (역할별):**

| 심볼 | 역할 |
| --- | --- |
| `PFM_TRY` / `PFM_TRYNJ` / `PFM_CATCH` | 래퍼 매크로 (TRY 패밀리) |
| `PFM_ERRC` / `PFM_ERRS` / `PFM_MSGE` | 에러 반환 |
| `PRINT_*` / `PFM_DBG` | 로깅·디버그 |
| `RC_OK` / `RC_NRM` / `RC_NFD` / `RC_ERR` | 반환 코드 상수 |
| `mpfmdbio_errno` / `mpfmdbio_errstr` / `mpfmdbio_reccnt` | DB 결과 메타 헬퍼 |

**예외 (prefix 매칭이지만 본문 의미 있음):**

| 심볼 | 역할 |
| --- | --- |
| `mpfmdbio` | DB 접근 — 1번째 인자 (테이블 / 시퀀스 / 패키지 식별자 리터럴) 보고 §3 처리 |
| `mpfm_dlcall` | 동적 모듈 dispatcher — 1번째 인자 (타깃 모듈명 string literal) 가 진짜 호출 대상 |

### 2.3 사용자 정의

위 두 그룹에 속하지 않는 모든 함수 호출 = 비즈니스 로직.

## 3. DB 접근 패턴

### 접근 메커니즘

`mpfmdbio(table_id_literal, &in_struct, &out_struct)` — **1번째 인자는 항상 문자열 리터럴** (상수 / `#define` 우회 없음). 1번째 인자는 **DBIO 식별자** (`<table>_<crud><4digit>` 포맷, DB 객체 키), 함수/모듈 이름 아님. **dispatcher 패턴 X**.

### Identifier Rule

DBIO 리터럴 형태: `<table_name>_<crud_letter><4-digit>`. 테이블명 추출 = trailing `_<crud><4-digit>` suffix strip. 여러 raw 리터럴이 같은 테이블명으로 정규화되는 게 정상 — 각 호출마다 별도 access edge 등록.

예: `"zpay_ap_rltm_auth_hst_u0001"` → 테이블 `zpay_ap_rltm_auth_hst`, CRUD `u`.

### 진짜 테이블 vs 비-테이블 객체 구분

`mpfmdbio()` 의 1번째 인자가 항상 테이블을 가리키는 건 아님 — DB 안에는 시퀀스 / 패키지 / 함수 / 뷰 등 다른 객체가 있을 수 있음. raw 이름만으로 "테이블" 단정 X — 실제 호출 단서를 같이 봐야.

**sequence DBIO** (예: `<table>_s_<crud><4digit>` 같이 가운데 `_s_` 추가) — 단서 4가지 일치 시 sequence 확정:

- raw 이름: 가운데 `_s_` 추가 (예: `zpay_ap_auth_req_tmp_s_s0001` 의 이중 `_s`)
- input 인자: `NULL` 또는 거의 비어 있음
- output 필드: `.nextval` (Oracle SEQUENCE 의 다음 값)
- 주변 주석 / 함수명: "채번", "sequence", "ser_num 발급" 등

→ sequence 는 테이블이 아님. **Table 노드 생성 X**. base table 만 일반 DBIO 호출에서 등록.

단순 trailing suffix strip 만으로 정규화하면 환각 발생 — 예: raw `zpay_ap_auth_req_tmp_s_s0001` 의 trailing `_s0001` 만 strip 하면 `zpay_ap_auth_req_tmp_s` 가 남아 마치 새 테이블처럼 보이지만 실제 객체는 sequence (테이블 X). 같은 base 의 일반 DBIO (`_i0001`, `_s0001`) 가 가리키는 `zpay_ap_auth_req_tmp` 가 진짜 테이블.

### Naming Rule (CRUD)

| suffix | op | relation |
| --- | --- | --- |
| `_s####` | SELECT | READS |
| `_i####` | INSERT | WRITES |
| `_u####` | UPDATE | WRITES |
| `_d####` | DELETE | WRITES |

(MERGE/UPSERT 가 ProFrame DBIO 에 별도 suffix 로 존재하면 본문에서 관찰 후 추가 — 추측 X.)

### 결과 / 에러

`RC_NRM` (또는 `RC_OK`) = 성공, `RC_NFD` = 데이터 없음, 그 외 = 에러 (`mpfmdbio_errno()` / `mpfmdbio_errstr()` 로 상세).

## 4. 동적 모듈 호출 패턴 (cross-module)

`mpfm_dlcall(target_literal, &io_struct)` — 1번째 인자 = **타깃 모듈명** string literal (예: `"zapamcom10060"`), 2번째 = `<name>_io_t` 통합 구조체. dispatcher wrapper — 진짜 호출 타깃은 1번째 literal 이고, 변수로 들어와 동적 결정될 땐 타깃 결정 불가.

## 5. 함수 명명 (참고)

DB 래퍼 함수 prefix 다양 (`b###_dbio_*`, `b###_check_*` 등 공존). **단일 prefix 단정 금지** — 식별은 함수 이름 prefix 가 아니라 `mpfmdbio` 호출 본문 기준.
