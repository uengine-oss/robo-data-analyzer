---
name: entity-analysis
description: >
  **언제**: 카테고리 = entity, bean, cmp_mapping, artifact, entity_fqn, candidates, profile_only.
  특히 `profile_only` (시작점 없이 profile_summary 만 주어진 경우) 도 이 스킬 담당.
  **작업**: 엔티티 / 빈 / 클래스 후보 / 프로파일 힌트를 시작점으로 코드에서 실제 DB 접근 함수를
  `bundle_search` 로 찾아 READS/WRITES · FK · 컬럼 엣지를 등록.
  **트리거 키워드**: 엔티티 추적, entity_fqn, class candidates, profile-summary 기반 수색,
  JPA Entity, XML 엔티티 매핑, DAO/Repository 분석.
  **사용 금지**: DDL 메타 있을 때 (`ddl`/`ddl_tables`) → `table-tracking`. SQL 키 → `sql-tracking`,
  설정 키 → `config-tracking`.
---

# Entity Analysis Skill

엔티티 / 클래스 후보 / 프로파일 힌트에서 코드의 실제 DB 접근 함수를 찾아 그래프 관계 생성.

## ⚠ 핵심 원칙

- **조기 종료 X**. 발견되는 만큼 빠짐없이 등록.
- 검색 0건 → 변형으로 재시도 (대소문자 / 부분 / 복수형 / 변환 suffix 유무). 한 번 만에 포기 X.
- 한 테이블 발견 후 그 테이블 사용하는 **모든 함수**를 `bundle_search(<table>, kind="text")` 로 reverse-search 필수.
- READS/WRITES 등록 후 본문의 struct/DTO/entity 필드를 같은 turn 에 `graph_create_column_tool` 등록.

---

## 카테고리 입력 처리

### ▶ category = profile_only

`data.items` 가 `[null]` 이어도 작업 있음. user 메시지의 `[Project Framework · DB Access Pattern Analysis — Search Map]` 섹션이 작업 source.

**Step 0 — profile 파싱**: profile_summary 에서 추출
- **접근 메커니즘**
- **Identifier Rule**
- **Naming Rule** (CRUD 결정 규칙)
- **ALL_CAPS 식별자** → 테이블명 후보
- **함수/매크로명 패턴** → 검색 키워드 후보

이후 아래 **탐색 시나리오** 진행.

### ▶ category = entity · bean · cmp_mapping · artifact

각 아이템: `{key, target_type, chunk_type, file_path, text_snippet}`. `text_snippet` 에서 클래스명 / 엔티티명 / 테이블명 추출 → 아래 시나리오 진행.

### ▶ category = entity_fqn (`com.acme.User`)

마지막 segment (클래스명) → 0건 시 전체 FQN → 부분 경로 → 짧은 이름 순으로 변형 재시도. ORM 의 Identifier Rule (`User` → `users` pluralization 등) 적용 → 아래 시나리오 진행.

### ▶ category = candidates

엔티티 판정 (✅ SQL/ORM 인자 / 1:1 컬럼 필드 / 데이터 클래스 어노테이션 / DB 매핑 메서드 — ❌ Enum/예외/설정/Service/Manager/Controller/Repository/Handler/abstract/interface skip). 엔티티 확정 → 클래스/FQN seed 로 시나리오 진행.

---

## 탐색 시나리오 (4가지 trail — 모두 독립 처리, 충돌 X)

테이블이 직접만 접근되는 경우 드뭄. **4가지 trail 각각 독립 시나리오**로 처리. 한 시나리오 발견했다고 다른 시나리오 skip X — 동시에 여러 trail 로 도달.

각 시나리오 공통:
- 등록 시 `table_name` 은 **Identifier Rule 적용 후 정규화된 이름**.
- 등록 시 같은 turn 에 본문 필드를 컬럼으로 함께 등록.
- 등록 후 follow-up 키워드 수집 (다른 리터럴 / 상수 / 변수 / include).

**모든 시나리오 placeholder 표기**:
- `<ACCESS_FN>` — 프로파일이 정의한 DB 접근 함수/매크로/메서드 이름.
- `<TABLE>` — 정규화된 테이블명.
- `<RAW_LIT>` — 코드에 등장하는 raw 리터럴 (Identifier Rule 적용 전).
- `<funcA>` / `<funcB>` — 코드의 함수 chunk_id.
- `<KEY>` — 변수/상수 이름.
- `<HELPER>` — 헬퍼/래퍼 함수 이름.
- `<MAPPER>` — mapper / Repository / DAO 인터페이스/메서드 이름.

### 시나리오 1: 직접 접근

**상황**: 테이블 리터럴이 함수 본문에 직접 등장.

**예시 흐름**:

```text
[Step 1] 접근 메커니즘으로 reverse-search
  bundle_search(query="<ACCESS_FN>", kind="text")
  → 결과: N 개 chunk_id (호출처 목록)

[Step 2] 각 chunk_id 마다 본문 확인
  bundle_get_chunk(chunk_id="<funcA>")
  → 본문 일부:
    ... <ACCESS_FN>("<RAW_LIT>", ...) ...

[Step 3] Identifier Rule 적용
  raw 리터럴: "<RAW_LIT>"
  Rule: profile 이 명시한 정규화 규칙
  → 정규화: "<TABLE>"

[Step 4] Naming Rule 적용
  profile 의 CRUD 결정 규칙
  → READS 또는 WRITES

[Step 5] 즉시 등록 (같은 turn)
  graph_create_table_access_tool(
    table_name="<TABLE>",
    access="<READS|WRITES>",
    evidence='<ACCESS_FN>("<RAW_LIT>", ...);',
    source_function_id="<funcA>"
  )

[Step 6] 본문 필드 → 컬럼 등록 (같은 turn)
  본문에 보이는 모든 struct/DTO/entity 필드:
  → graph_create_column_tool(table="<TABLE>", column="<field_name>", dtype="<type>", ...)
  → ... (필드 모두)

[Step 7] 다음 chunk_id 로
```

### 시나리오 2: 변수 / 상수 경유

**상황**: 리터럴이 변수 / 상수 / `#define` / `static const` / config 값에 담겨 있고, 다른 함수가 그 변수명으로 DB 호출.

**예시 흐름**:

```text
[Step 1] 시나리오 1 진행 중 본 본문에서 변수 참조 발견
  본문 일부:
    <const_decl> <KEY> = "<RAW_LIT>";
    <funcType> <funcA>(...) {
      ...
      <ACCESS_FN>(<KEY>, ...);   ← 변수로 호출 (리터럴 직접 X)
      ...
    }

[Step 2] 변수/상수명으로 reverse-search
  bundle_search(query="<KEY>", kind="text")
  → 결과: M 개 chunk_id (변수 정의 + 변수 사용 함수들)

[Step 3] 각 결과 본문 확인
  bundle_get_chunk(chunk_id="<funcB>")
  → 본문에서 <KEY> 가 <ACCESS_FN> 호출에 쓰이는지 확인.

[Step 4] Identifier Rule 적용
  변수의 정의 라인 (Step 1) 에서 본 리터럴: "<RAW_LIT>"
  → 정규화: "<TABLE>"

[Step 5] 등록 — 변수 사용해 DB 호출하는 함수에 (변수 정의 함수 X)
  graph_create_table_access_tool(
    table_name="<TABLE>",
    access="<READS|WRITES>",
    evidence='<ACCESS_FN>(<KEY>, ...);  // <KEY> = "<RAW_LIT>"',
    source_function_id="<funcB>"   ← 사용자!
  )
```

### 시나리오 3: 헬퍼 / 래퍼 경유

**상황**: 일반 헬퍼 함수가 테이블 리터럴을 **인자로 받아** 실제 접근. 여러 caller 가 자기 테이블 인자로 호출.

**예시 흐름**:

```text
[Step 1] 시나리오 1 진행 중 본 본문에 헬퍼 호출 패턴 발견
  본문 일부 (<funcA>):
    <ACCESS_FN_alt>("<RAW_LIT>", ...);   ← 다른 함수가 ACCESS_FN 을 한 번 더 감쌈

[Step 2] 헬퍼 함수명으로 reverse-search
  bundle_search(query="<HELPER>", kind="text")
  → 결과: K 개 chunk_id
    - <HELPER> (정의 자체)
    - <funcA>, <funcB>, <funcC>, ... (호출 caller 들)

[Step 3] 헬퍼 정의 본문 확인 (헬퍼 자체에는 등록 X)
  bundle_get_chunk(chunk_id="<HELPER>")
  → 본문: <returnType> <HELPER>(<param_table>, ...) { <ACCESS_FN>(<param_table>, ...); }
  → 일반 plumbing, 의미 의도 X. 등록 X.

[Step 4] 각 caller 본문 확인 → caller 가 어떤 테이블 인자로 호출하는지 추출
  bundle_get_chunk(chunk_id="<funcB>")
  → 본문: <HELPER>("<RAW_LIT>", ...);
  → 인자 리터럴: "<RAW_LIT>"
  → Identifier Rule 적용 → "<TABLE>"

[Step 5] caller 함수에 등록 (헬퍼 자체 X)
  graph_create_table_access_tool(
    table_name="<TABLE>",
    access="<READS|WRITES>",
    evidence='<HELPER>("<RAW_LIT>", ...);',
    source_function_id="<funcB>"   ← caller!
  )

  → 다른 caller (<funcC>, <funcD>, ...) 들도 같은 방식
```

### 시나리오 4: mapper / repository / DAO 경유

**상황**: mapper 정의 / Repository 메서드 / DAO 가 접근 추상화. service / use-case / controller 가 호출.

**예시 흐름**:

```text
[Step 1] mapper / Repository 정의 발견
  bundle_search(query="<MAPPER>", kind="symbol")
  → 결과: 1+ chunk_id (인터페이스 정의)
  bundle_get_chunk(chunk_id="<MAPPER>")
  → 본문: 메서드 목록 (예: findById, save, update, delete)

[Step 2] 각 메서드 호출처 reverse-search
  bundle_search(query="<mapperInstance>.<methodName>", kind="text")
    or bundle_search(query="<methodName>", kind="symbol")
  → 결과: P 개 chunk_id (caller 들 — service, use-case, controller)

[Step 3] Identifier Rule 적용 (ORM 의 경우)
  엔티티명: <Entity>
  Rule: profile 이 명시한 정규화 규칙
  → 정규화: "<TABLE>"

[Step 4] Naming Rule 적용
  메서드명: <methodName>
  Rule: profile 이 명시한 CRUD 매핑
  → READS 또는 WRITES

[Step 5] 각 caller 에 등록 (mapper 자체 X)
  graph_create_table_access_tool(
    table_name="<TABLE>",
    access="<READS|WRITES>",
    evidence="<mapperInstance>.<methodName>(...);",
    source_function_id="<callerFunc>"   ← caller!
  )
  → 다른 caller 동일 처리. 다른 메서드 (save, update, delete 등) 도 동일 흐름.
```

---

## 다대다 강제 — 등록 테이블마다 reverse-search

각 distinct 정규화 테이블명마다:

```text
bundle_search(<정규화_테이블명>, kind="text")
→ 결과의 모든 새 chunk_id 본문 확인
→ 각 chunk_id 마다 위 4가지 시나리오 중 해당하는 것 적용
```

🚨 **이 단계 빼면 다대다 실패 확정**. 시나리오 1 (직접) 만으로는 일부 함수만 잡힘 — 시나리오 2/3/4 가 간접 접근 함수를 잡음.

검색 결과의 chunk_id 만 보고 "다 커버됨" 결론 X — 본문 열어서 판정.

---

## Trail 별 STOP 조건

각 시나리오 trail 마다:
- (a) CRUD 실행 함수 본문 도달, 또는 (b) 그 함수 호출 지점 도달
→ 엣지 등록 → **이 trail 만** 종료. 호출 체인 무한 역추적 X. 다른 trail 있으면 새로 시작.

---

## 등록 규칙

### READS / WRITES

- `table_name`: **정규화** (Identifier Rule 적용 후). raw 리터럴 그대로 X.
- `source_function_id`: `bundle_search` 결과 실제 chunk_id.
- `evidence`: 접근 코드 라인 정확한 인용.
- `access`: READS (SELECT) / WRITES (INSERT/UPDATE/DELETE).

### 컬럼

**원칙**: **최대한 많이 찾도록 노력**. "중요한 것만" / "일부만" / "대표 몇 개만" 같은 자체 판단 누락 X. 같은 컬럼 중복 등록 무해 — 빠뜨리는 게 실패.

찾을 곳:
- struct/DTO 멤버 접근 (`obj.field`, `obj->field`).
- ORM annotation 매핑 (`@Column(name=...)`, `@Field`, 매핑 어노테이션 필드).
- mapper/repository 메서드의 파라미터·결과 매핑 컬럼.
- SQL 리터럴 내부 컬럼 (SELECT 절, WHERE 절, INSERT VALUES, UPDATE SET).
- 변수/구조체 타입 정의 추적 — 함수가 그 타입을 사용 중이면 그 타입의 멤버도 등록 대상.

**의심되면 추가 조사**:
- 컬럼명 추정만으로 끝내지 말 것. `bundle_search("<column_name>", kind="text")` / `bundle_search("<table_name>", kind="text")` 로 사용처·정의 본문 직접 확인.
- 매크로·define 으로 감싸진 컬럼 (`MACRO(in, X)` 같은 비정규 패턴) 도 매크로 정의를 열어 멤버 추출.
- 본문 내 변수의 타입을 모르면 `bundle_search(query="<typeName>", kind="symbol")` 로 정의 본문 확보 후 멤버 등록.

등록은 `graph_create_column_tool(table_name, column_name, dtype)`. column_name 은 case 통일만 (Identifier Rule 의 정규화 적용 후).

#### dtype

코드 단서 우선순위:
1. **DDL 메타 (있으면 최우선)** — `table_columns` 도구 결과 그대로.
2. **선언/정의 타입** — 멤버 변수 타입, 필드 어노테이션, DTO 속성, type hint.
3. **길이/제약 단서** — sizeof, 길이 매크로/인자, NOT NULL 검증.
4. **사용 패턴** — 변환 함수 (parse/convert 류), 비교 리터럴.

코드 단서 0 이면 빈 문자열 (추측 X). description 은 작성 X (Phase 3 analysis 가 단일 책임).

### FK

- `from_table` = FK 컬럼 쪽 (자식), `to_table` = PK 쪽 (부모). 둘 다 정규화.
- 자기 참조 X. 중복 X. 추측 X.

---

## 안티패턴 (절대 X)

- 🚫 Identifier Rule 적용 안 한 raw 리터럴 그대로 `table_name` 등록.
- 🚫 같은 테이블로 정규화되는 여러 리터럴을 별개 테이블로 등록.
- 🚫 다수 함수가 만지는 테이블에 1 함수만 등록 (다대다 미달).
- 🚫 reverse-search 단계 skip — 가장 흔한 불완전 커버 원인.
- 🚫 같은 chunk_id 재호출 (응답 동일).
- 🚫 컬럼 다음 phase 로 미루기 — 본문은 지금만 보임.
- 🚫 시나리오 1 (직접) 됐다고 시나리오 2/3/4 skip.

---

## 완료 체크리스트

- [ ] profile / 시작점의 모든 후보 처리.
- [ ] 4가지 시나리오 모두 시도 (직접 / 변수경유 / helper경유 / mapper경유).
- [ ] **등록한 모든 테이블 reverse-search** (다대다 강제).
- [ ] 모든 R/W 함수 본문 필드 컬럼 등록.
- [ ] 0-hit 키워드 변형 재시도 완료.
- [ ] 모든 `table_name` 정규화 (raw 리터럴 그대로 X).
- [ ] 등록 엣지 수 0 이 아님 (시작점 / 프로파일 단서가 있는데 0 건이면 빠뜨림 의심).

전부 YES → 완료. 하나라도 NO → 계속.
