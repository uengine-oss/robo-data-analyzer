---
name: entity-analysis
description: >
  **언제 사용**: 사용자 메시지의 `카테고리:` 값이 entity, bean, cmp_mapping, artifact,
  entity_fqn, candidates, profile_only 중 하나일 때 **반드시** 이 스킬을 로드할 것.
  특히 `profile_only` — 시작점 정보 없이 프로파일 요약만 주어진 경우에도 이 스킬이 담당.
  **작업 내용**: 엔티티·빈·클래스 후보·프로파일 힌트를 시작점으로 코드에서 실제 DB 접근
  함수를 bundle_search 로 찾아, READS/WRITES · FK · 컬럼 엣지를 등록한다.
  **트리거 키워드**: 엔티티 추적, entity_fqn, class candidates, profile summary 기반 수색,
  JPA Entity, XML 엔티티 매핑, DAO/Repository 분석.
  **사용 금지 (Do NOT)**: DDL 테이블 메타데이터가 주어진 경우(category=ddl, ddl_tables) —
  그때는 `table-tracking` 스킬을 사용. 미매칭 SQL 키 추적은 `sql-tracking`, 설정 키 추적은
  `config-tracking` 사용.
---

# Entity Analysis Skill

## ⚠ 중요 원칙 (모든 카테고리 공통)

- **절대 조기 종료 금지**. 하나/두 개 등록했다고 멈추지 말 것.
  일반적인 업무 모듈은 **10~20개 이상의 테이블 접근**이 있다. "없는 것 같다" 라는 느낌으로 종료 금지.
- 검색 결과 0건 → **검색어를 바꿔 최소 3회 재시도**. 대소문자·언더스코어·부분문자열·복수형 전부 시도.
- **모든 테이블 접근 함수를 찾을 때까지** 계속 탐색. 테이블 이름이 1개 나오면 그 테이블을 쓰는 **모든 함수**를 `bundle_search(table_name, kind="text")` 로 반드시 수색.
- READS/WRITES 등록 후 **반드시** 해당 함수의 구조체/DTO 필드를 확인하고 테이블 연결 근거가 있으면 `graph_create_column_tool` 호출.

---

## 카테고리별 처리 — 상황에 맞는 정확한 순서

### ▶ category = profile_only (시작점 없음, 프로파일 지침만 있음)

**핵심 인식**: `data.items` 가 `[null]` 이어도 **작업 있음**. 작업 대상은 **user message 의 `[프로젝트 프레임워크·DB 접근 패턴 분석]` 섹션 (= profile_summary)**. 이 prose 텍스트에서 테이블명과 접근 함수를 추출해 코드 탐색의 출발점으로 삼는다.

**단계 0 — 프로파일 파싱 (가장 먼저)**
profile_summary 를 읽으며 아래를 모두 추출해 목록으로 만든다:
- **ALL_CAPS 식별자** (예: `ZNGM_COMM_CD_DTL`, `ZPAY_AP_RLTM_AUTH_HST`) → 테이블명 후보
- **함수명/매크로** (예: `rltm_auth_his_i0002`, `b200_dbio_*`, `PFM_TRY`) → DB 접근 함수 후보
- **패턴 키워드** (예: `dbio_`, `_i0002`, `_s_get`) → 명명 규칙 힌트

**단계 1 — 코드 수색 (각 후보마다 반복)**
```
For each 테이블 후보 T in 추출목록:
    결과 = bundle_search(T, kind="text")
    For each 검색결과 R:
        코드 = bundle_get_chunk(R.chunk_id)
        If R.함수가 T 를 실제 CRUD 하면:
            graph_create_table_access_tool(T, READS|WRITES, evidence, R.chunk_id)
```

**단계 2 — 확장 수색 (패턴 키워드 기반)**
profile_summary 에 `dbio_` 같은 명명 패턴이 있으면 `bundle_search("dbio_", kind="symbol")` 로 이 패턴 함수들을 모두 찾고, 각 함수 본문에서 테이블명을 추출해 **추가** 등록.

**단계 3 — 완료 조건**
- profile_summary 에 언급된 **모든** 테이블 후보 확인 완료
- 각 테이블에 대해 **모든 접근 함수** 등록 완료 (1개 찾고 멈추지 말 것)
- 컬럼 등록도 가능한 만큼 수행

**절대 금지**:
- `items=[null]` 보고 "할 일 없음" 으로 종료하는 것
- 테이블 1~2개 찾고 만족하는 것
- 검색 0건 시 한 번 만에 포기하는 것

---

### ▶ category = entity · bean · cmp_mapping · artifact (미매칭 인덱스 키)

각 아이템은 `{key, target_type, chunk_type, file_path, text_snippet}` 를 가짐.

**단계 1 — 식별자 추출**: `text_snippet` 에서 클래스명·엔티티명·테이블명을 파악.

**단계 2 — 사용처 수색** (각 식별자마다):
- `bundle_search(식별자, kind="text")`
- `bundle_search(식별자, kind="symbol")`
- 매칭 0건이면 부분 문자열·짧은 이름으로 재시도 (최소 3회)

**단계 3 — 패턴 판별 + 등록**: 각 결과 코드를 읽어 CRUD 인지 확인 → `graph_create_table_access_tool`.

**단계 4 — FK**: 원문·코드에서 FK 관계 보이면 `graph_create_fk_tool` 등록.

**단계 5 — 컬럼**: READS/WRITES 확정 함수의 DTO/구조체 필드를 `graph_create_column_tool` 로 등록.

---

### ▶ category = entity_fqn (Java 엔티티 FQN)

각 아이템은 FQN 문자열 (예: `com.acme.User`).

**단계 1**: FQN 의 마지막 세그먼트(클래스명) 로 `bundle_search` 시작.
**단계 2**: 0건이면 전체 FQN / 부분 경로 / 짧은 이름 순으로 재시도.
**단계 3**: 엔티티 클래스 사용자(Repository/DAO/Service) 추적 → 실제 CRUD 함수에 엣지.
**단계 4**: 각 함수의 DTO 필드를 컬럼으로 등록.

---

### ▶ category = candidates (후보 클래스/구조체)

각 아이템: `{fqn, file_path, annotations, comment}`.

**단계 1 — 엔티티 판정**: 아래 기준으로 엔티티 여부 판정. 엔티티 아니면 스킵.
**단계 2 — 수색**: 엔티티 확정되면 클래스명/FQN 으로 `bundle_search`.
**단계 3 — 등록**: 실제 DB 접근 함수에 엣지 + 컬럼.

#### 엔티티 판정 기준

✅ **엔티티 시그널** (하나 이상 해당):
- SQL 문에서 대응 테이블이 사용됨
- ORM/DB 함수의 인자로 사용됨
- 필드가 DB 컬럼과 1:1 대응 (id, created_at, status 등)
- 데이터 클래스 어노테이션 보유
- DB 매핑 메서드 보유

❌ **엔티티 아님** (스킵):
- Enum / 예외 / 설정 / Service/Manager/Controller/Repository/Handler / 추상클래스 / 인터페이스

---

## 체인 추적 패턴 (모든 카테고리에서 활용)

시작점이 코드에서 어떻게 쓰이는지에 따라:

1. **직접** — 함수 본문에 시작점 식별자 등장 → **그 함수**에 엣지
2. **변수 경유** — 시작점이 변수/상수에 담김 → `bundle_search` 로 변수 참조 추적 → 참조 함수에 엣지
3. **Helper 경유** — 시작점 받는 helper 존재 → helper 의 **호출자**에 엣지 (helper 자체 X)
4. **매퍼/레포 경유** — 매퍼 정의/Repository 메서드 경유 → **호출자**에 엣지

### STOP 조건 (각 갈래별)

한 테이블에 여러 사용 갈래가 있다. 각 갈래마다:
- (a) CRUD 실행 함수 본문 만남, **또는**
- (b) 그 함수를 호출하는 지점 만남

→ "이 함수가 이 테이블을 사용" 확정. 엣지 등록 후 **이 갈래만** 종료.
→ 무한 역추적 금지. **다른 갈래 있으면 반드시 새로 시작**.

---

## 등록 규칙 (도구 호출 시 준수)

### READS/WRITES
- `table_name`: 실제 테이블명 (대문자 규칙 그대로).
- `source_function_id`: **반드시** `bundle_search` 로 발견한 실제 chunk_id.
- `evidence`: 코드 라인 직접 인용.
- `access`: READS (SELECT/조회) 또는 WRITES (INSERT/UPDATE/DELETE).

### 등록 직전 자체 검증
> "이 함수의 코드에서 이 테이블의 접근 근거를 확인했는가? 구체적 코드가 무엇인가?"

즉답 불가 → 등록 금지.

### 컬럼 (**중요 — 누락 금지**)
READS/WRITES 등록한 함수마다:
1. 함수 내에서 사용되는 구조체/DTO 필드를 확인
2. 해당 필드가 테이블 컬럼에 대응하면 `graph_create_column_tool(table_name, column_name, dtype, description)` 호출
3. 근거 없는 필드는 등록 금지, **있는 필드는 반드시 등록**

### FK
- `from_table` = FK 컬럼 쪽(자식), `to_table` = PK 쪽(부모).
- 자기 참조·중복 금지.
- 확신 없으면 생략.

---

## 공통 완료 체크리스트

작업 종료 전 self-check:
- [ ] profile_summary / 시작점에 언급된 **모든** 테이블/엔티티 후보 확인함?
- [ ] 각 테이블마다 **모든** 접근 함수 찾았나? (1개로 만족 금지)
- [ ] 검색 0건이던 키워드를 **최소 3회** 재검색했나?
- [ ] READS/WRITES 등록한 함수마다 컬럼 검토 완료?
- [ ] 근거 부족 관계는 등록 안 했나?

모두 예 → 종료. 하나라도 아니오 → 작업 계속.
