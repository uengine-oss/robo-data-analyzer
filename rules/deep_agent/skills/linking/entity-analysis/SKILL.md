---
name: entity-analysis
description: >
  **언제**: 카테고리 = `entity`, `bean`, `cmp_mapping`, `artifact`, `entity_fqn`,
  `candidates`, `profile_only`. 특히 `profile_only` (시작점 없이 profile_summary 만 주어진 경우) 도 담당.
  **작업**: 엔티티 / 빈 / 클래스 후보 / 프로파일 힌트를 시작점으로 코드의 실제 DB 접근 함수를
  찾아 **READS / WRITES / FK / Column** 엣지 등록.
  **트리거 키워드**: 엔티티 추적, entity_fqn, class candidates, profile-summary 기반 수색, JPA Entity, XML 엔티티 매핑, DAO/Repository 분석.
  **사용 금지**: DDL 메타 있을 때 (`ddl` / `ddl_tables`) → `table-tracking`. SQL 키 → `sql-tracking`, 설정 키 → `config-tracking`.
---

# Entity Analysis Skill

엔티티 / 클래스 후보 / 프로파일 힌트에서 코드의 실제 DB 접근 함수를 찾아 **READS / WRITES / FK / Column** 등록.

> 공통 절대 원칙·4-trail 탐색 원리·공통 안티패턴은 `prompts/linking/system.md` §3·§4·§5 참조. 이 SKILL 은 **이 카테고리 고유 사항** 만 명시.

---

## 1. 카테고리별 입력 처리

### `profile_only`

`data.items` 가 `[null]` 이어도 작업 있음. user 메시지의 `[Project Framework · DB Access Pattern Analysis — Search Map]` 섹션이 작업 source.

**Step 0 — profile 파싱**: profile_summary 에서 추출

- **접근 메커니즘** → 검색 키워드.
- **Identifier Rule** → raw 리터럴 → 정규화 테이블명 변환 룰.
- **Naming Rule** → CRUD 결정 룰.
- **ALL_CAPS 식별자 / 함수·매크로명 패턴** → 검색 키워드 후보.

이후 system.md 의 4-trail 진행.

### `entity` · `bean` · `cmp_mapping` · `artifact`

각 아이템: `{key, target_type, chunk_type, file_path, text_snippet}`. `text_snippet` 에서 클래스명 / 엔티티명 / 테이블명 추출 → 4-trail 진행.

### `entity_fqn` (`com.acme.User`)

마지막 segment (클래스명) → 0건 시 전체 FQN → 부분 경로 → 짧은 이름 순 변형 재시도. ORM 의 Identifier Rule (`User` → `users` pluralization 등) 적용 후 4-trail 진행.

### `candidates`

엔티티 판정:

- ✅ SQL/ORM 인자 / 1:1 컬럼 필드 / 데이터 클래스 어노테이션 / DB 매핑 메서드.
- ❌ Enum / 예외 / 설정 / Service / Manager / Controller / Repository / Handler / abstract / interface.

엔티티 확정 → 클래스 / FQN seed 로 4-trail 진행.

---

## 2. 등록 엣지

### READS / WRITES — 정확한 CRUD op 명시

- `table_name` = **정규화** (Identifier Rule 적용 후). raw 리터럴 그대로 X.
- `source_function_id` = `bundle_search` 결과 실제 chunk_id.
- `evidence` = 접근 코드 라인 정확한 인용.
- `access` = **정확한 CRUD op** — 통합 라벨 X. 다음 5 값 중 하나로 명시:
  - `"SELECT"` → READS 자동 유도
  - `"INSERT"` / `"UPDATE"` / `"DELETE"` / `"MERGE"` (UPSERT) → WRITES 자동 유도

  CRUD op 결정 근거는 **profile 의 Naming Rule** (호출 식별자 suffix / ORM 메서드명 / SQL keyword 등 — 프로젝트별 패턴). 코드 단서 0 이면 통합 라벨 `"READS"` / `"WRITES"` 호환.

### FK

- `from_table` = FK 컬럼 쪽 (자식), `to_table` = PK 쪽 (부모). 둘 다 정규화.
- 자기 참조 X. 중복 X. 추측 X.

### 비-테이블 객체 식별 (등록 X)

DB 접근 호출 (raw SQL · 매크로 인자 · ORM 메서드) 이 항상 테이블을 가리키는 건 아님. 다음은 **테이블 등록 X**:

| 객체 | 일반 식별 단서 |
| --- | --- |
| **sequence** | 입력 비어있고 출력은 단일 numeric / 명시적 시퀀스 함수 호출 |
| **stored procedure / function** | 식별자가 함수·프로시저 호출 (DDL 메타에 없음) |
| **view** | 이름이 DDL 메타에 없고 SELECT-only 컨텍스트 |

**환각 방지**: profile 또는 라이브러리 SKILL 의 "비-테이블 식별 단서" (구체 마커·접두/접미·호출 패턴) 가 명시돼 있으면 그것을 우선 적용.

---

## 3. 컬럼 등록 (이 SKILL 만 담당)

테이블 R/W 등록 직후 같은 turn 에 `graph_create_column_tool` 호출. **빠뜨리는 게 실패** — 같은 컬럼 중복 등록 무해, 누락이 치명.

### 필수 절차 (R/W 등록한 함수마다 반복)

1. 함수 본문에서 사용된 **입력/출력 데이터 구조체** (DTO / record / DBIO struct / mapper result type 등) 의 정의를 `bundle_search(query="<type_name>", kind="symbol")` 로 가져옴.
2. 그 구조체의 **모든 멤버 필드를 한 번에 등록**. 본문에서 직접 접근하지 않은 필드도 포함 — **구조체와 테이블이 1:1 매핑이면 멤버 = 컬럼**.
3. 본문의 SQL 리터럴 / 매크로 인자 / 직접 멤버 접근 (`obj.field`, `obj->field`) 도 추가 등록.

### R/W 지점 기반 컬럼 누적 (한 테이블 = 여러 R/W 위치)

테이블의 각 R/W 호출 위치마다:

1. **R/W 직전 본문** — 호출에 넘기는 입력 구조체 / 파라미터 / 변수가 어떻게 채워지는지 흐름 추적. 멤버에 할당되는 필드 = 컬럼.
2. **호출 체인 거슬러 올라가기** — R/W 함수의 caller 본문도 봄. caller 가 어떤 변수를 R/W 함수에 넘기는지 / 그 변수 출처. 컬럼 데이터 원천까지.
3. **반환값 / 출력 구조체 사용처** (R 의 경우) — 호출자가 어떤 필드 단위로 꺼내 쓰는지.

같은 테이블이 여러 R/W 위치에 등장 → 각각 반복 → **컬럼 누적**. 한 R/W 만 봐서는 일부 컬럼만 잡힘 (각 함수가 다른 컬럼 부분집합 만지는 게 일반적).

### 찾을 곳 (어디든 보이면 등록)

- **DB 입출력 구조체의 모든 멤버** (구조체와 테이블이 1:1 매핑이면 멤버 = 컬럼).
- struct / DTO 멤버 접근 (`obj.field`, `obj->field`).
- ORM 어노테이션 매핑 (`@Column(name=...)`, `@Field`, 매핑 어노테이션 필드).
- mapper / repository 메서드의 파라미터·결과 매핑 컬럼.
- SQL 리터럴 내부 컬럼 (SELECT 절, WHERE 절, INSERT VALUES, UPDATE SET).
- 변수/구조체 타입 정의 추적 — 함수가 그 타입을 사용 중이면 그 타입의 멤버도 등록 대상.
- 매크로·define 으로 감싸진 컬럼의 매크로 정의 열어 멤버 추출.

### 의심되면 즉시 추가 조사 (의심 = 누락 신호)

- 컬럼명 추정만 X. `bundle_search("<column_name>", kind="text")` / `bundle_search("<table_name>", kind="text")` 로 사용처·정의 본문 직접 확인.
- 본문 내 변수의 타입을 모르면 `bundle_search(query="<typeName>", kind="symbol")` 로 정의 본문 확보 후 멤버 등록.

### 등록 원칙

- `column_name` = 코드 등장 그대로 (case 통일 외 변환 X). 단일 테이블만 — 그 호출이 가리키는 테이블 외 X.
- `dtype` 단서 우선순위:
  1. DDL 메타 (있으면 최우선) — `table_columns_tool` 결과.
  2. 선언/정의 타입 — 멤버 변수 타입, 필드 어노테이션, DTO 속성, type hint.
  3. 길이/제약 단서 — `sizeof`, 길이 매크로/인자, NOT NULL 검증.
  4. 사용 패턴 — 변환 함수 (parse/convert), 비교 리터럴.

  코드 단서 0 → 빈 문자열 (추측 X). description 작성 X (Phase 3 analysis 단일 책임).

---

## 4. 카테고리 특유 안티패턴

- 🚫 R/W 함수 본문만 보고 컬럼 등록 종료 — **입력/출력 구조체 정의·R/W 직전 본문·caller 흐름 미추적 시 컬럼 누락**.
- 🚫 한 R/W 위치만 보고 그 테이블 컬럼 등록 끝냄 — 같은 테이블의 다른 R/W 위치 무시 시 컬럼 부분집합만 잡힘.
- 🚫 본문에 등장한 식별자만 등록 — 구조체 멤버 전체 가져오지 않으면 캡슐화된 필드 누락.
- 🚫 본문에 안 등장한 컬럼 추측 등록.
- 🚫 다른 테이블의 IO struct 필드를 같은 테이블 컬럼으로 등록.
- 🚫 컬럼 다음 phase 로 미루기 — 본문은 지금만 보임.

---

## 5. 완료 체크리스트 (이 카테고리 고유)

- [ ] profile / 시작점의 모든 후보 처리.
- [ ] 등록한 모든 테이블 reverse-search (다대다 강제) — `bundle_search(<table>, kind="text")`.
- [ ] 모든 R/W 함수 본문 필드 컬럼 등록 — 입력/출력 구조체 정의 가져와 멤버까지.
- [ ] 같은 테이블의 모든 R/W 위치 컬럼 누적.
- [ ] R/W 직전 본문 / caller 호출 체인 흐름 추적해 컬럼 출처까지 등록.
- [ ] 모든 `table_name` 정규화 (raw 리터럴 그대로 X).
- [ ] 등록 엣지 수 0 이 아님 (시작점 / profile 단서 있는데 0 건이면 빠뜨림 의심).

전부 YES → 완료. 하나라도 NO → 계속.
