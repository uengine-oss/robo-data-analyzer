---
name: config-tracking
description: >
  **언제**: 카테고리 = `config`. data 에 미매칭 설정 키 (config_key_path, file_path) 포함.
  **작업**: 각 config 키를 코드에서 추적 → 참조하는 **모든 함수** 에
  `(FUNCTION)-[:USES_CONFIG]->(ConfigSection)` 관계 생성.
  **트리거 키워드**: application.yml/properties, config_lookup_tool, getProperty, @Value,
  environment variable, 미매칭 ConfigSection, USES_CONFIG.
  **사용 금지**: 엔티티/클래스 → `entity-analysis`, DDL → `table-tracking`, SQL 키 → `sql-tracking`.
---

# Config Tracking Skill

미매칭 설정 키를 코드에서 추적해 USES_CONFIG 관계 생성.

## ⚠ 핵심 원칙

- **조기 종료 X**. 한 키를 여러 함수가 참조 가능 — 빠짐없이 등록.
- 검색 0건 → 변형으로 재시도. 한 번 만에 포기 X.
- `source_function_id` 는 반드시 `bundle_search` 결과 실제 chunk_id.

## 입력 데이터

각 아이템: `{key, target_type, file_path}`.
먼저 `config_lookup_tool(key)` → 설정 값 / file_path 확보.

---

## 탐색 시나리오 (4가지 trail — 모두 독립 처리)

각 config_key 마다 4가지 trail 모두 시도. 한 trail 발견했다고 다른 trail skip X.

**placeholder**:
- `<KEY>` — config 키 전체 경로 (예: `database.url`).
- `<LAST>` — 키의 마지막 segment (예: `url`).
- `<FILE>` — 설정 파일명 (예: `application.yml`).
- `<funcA>`, `<funcB>` — 함수 chunk_id.
- `<CONST>` — 키를 담은 상수 / 변수 이름.
- `<HELPER>` — Config / Properties 헬퍼 클래스 / 메서드.

### 시나리오 1: 직접 — getProperty 형태 호출

**상황**: 키 문자열이 함수 본문에 직접 등장 (`getProperty("database.url")` 등).

```text
[Step 1] 키 전체로 검색
  bundle_search(query="<KEY>", kind="text")
  → 결과: N 개 chunk_id

[Step 2] (보강) 키 마지막 segment 로도 검색
  bundle_search(query="<LAST>", kind="text")
  → 결과 합치기

[Step 3] 각 chunk_id 본문 확인
  bundle_get_chunk(chunk_id="<funcA>")
  → 본문 일부:
    String url = config.getProperty("<KEY>");
    또는: System.getenv("<KEY>")

[Step 4] 즉시 등록
  graph_create_refer_to_tool(
    target_type="ConfigSection",
    target_key="<KEY>",
    evidence='config.getProperty("<KEY>");',
    source_function_id="<funcA>"
  )
  → USES_CONFIG 엣지 생성.
```

### 시나리오 2: 상수 / 변수 경유

**상황**: 키가 상수 / `static final String` 에 담겨 있고, 다른 함수가 그 상수로 config 호출.

```text
[Step 1] 시나리오 1 진행 중 본 본문에서 상수 정의 발견
  본문 일부:
    public static final String <CONST> = "<KEY>";
    long <funcA>(...) {
      String value = config.getProperty(<CONST>);   ← 상수로 호출
    }

[Step 2] 상수명으로 reverse-search
  bundle_search(query="<CONST>", kind="text")
  → 결과: 정의 + 사용 함수들

[Step 3] 각 사용 본문 확인
  bundle_get_chunk(chunk_id="<funcB>")
  → 본문에서 <CONST> 가 config 호출에 쓰이는지 확인.

[Step 4] 등록 — 상수 사용 함수에 (정의 함수 X)
  graph_create_refer_to_tool(
    target_type="ConfigSection",
    target_key="<KEY>",
    evidence='config.getProperty(<CONST>);  // <CONST> = "<KEY>"',
    source_function_id="<funcB>"   ← 사용자
  )
```

### 시나리오 3: ConfigHelper / 래퍼 경유

**상황**: ConfigHelper / Properties 헬퍼 클래스가 config 키를 인자로 받아 조회.

```text
[Step 1] 시나리오 1/2 진행 중 헬퍼 호출 패턴 발견
  본문 일부 (<funcA>):
    String value = <HELPER>.get("<KEY>");   ← <HELPER> 가 config 헬퍼

[Step 2] 헬퍼 함수명으로 reverse-search
  bundle_search(query="<HELPER>", kind="text")
  → 결과: 헬퍼 정의 + caller 들

[Step 3] 헬퍼 정의 본문 확인 (헬퍼 자체 X — 일반 plumbing)
  bundle_get_chunk(chunk_id="<HELPER>")
  → 본문: 헬퍼가 properties 또는 yaml 에서 조회만 함
  → 등록 X.

[Step 4] 각 caller 본문 확인 → 어떤 키 인자로 호출?
  bundle_get_chunk(chunk_id="<funcB>")
  → 본문: <HELPER>.get("<KEY>");
  → 인자 키: "<KEY>"

[Step 5] caller 에 등록 (헬퍼 자체 X)
  graph_create_refer_to_tool(
    target_type="ConfigSection",
    target_key="<KEY>",
    evidence='<HELPER>.get("<KEY>");',
    source_function_id="<funcB>"   ← caller
  )
```

### 시나리오 4: 어노테이션 / DI 주입 경유 (`@Value` / `@ConfigurationProperties`)

**상황**: Spring `@Value("${database.url}")` / `@ConfigurationProperties` 가 필드/생성자 인자로 키 주입.

```text
[Step 1] 어노테이션 검색
  bundle_search(query="@Value", kind="text")
    또는: bundle_search(query="@ConfigurationProperties", kind="text")
  → 결과: 어노테이션 사용 chunk_id (필드 / 생성자 / 클래스)

[Step 2] 각 본문 확인 → 어노테이션 값 추출
  bundle_get_chunk(chunk_id="<classOrField>")
  → 본문 일부:
    @Value("${<KEY>}")
    private String url;
    또는: @ConfigurationProperties(prefix = "<KEY>")

[Step 3] 키 매칭 확인
  어노테이션 값이 "<KEY>" 또는 "<KEY>.something" 패턴이면 등록 후보.

[Step 4] 등록 대상 결정
  - `@Value` 가 필드: 해당 클래스의 메서드 (또는 생성자) 가 caller — 그 메서드 chunk_id 에 등록.
  - `@ConfigurationProperties` 가 클래스: 클래스 자체 또는 그 인스턴스 사용하는 메서드 에 등록.

[Step 5] 등록
  graph_create_refer_to_tool(
    target_type="ConfigSection",
    target_key="<KEY>",
    evidence='@Value("${<KEY>}") private String url;',
    source_function_id="<classOrMethod>"
  )
```

### Trail 별 STOP 조건

각 trail 마다 (a) 실제 키 사용 함수 도달, 또는 (b) 그 함수의 호출자 도달 → 엣지 등록 → 이 trail 만 종료. 무한 역추적 X.

---

## 안티패턴 (절대 X)

- 🚫 1개 caller 만 등록하고 종료.
- 🚫 시나리오 1 (직접) 됐다고 시나리오 2/3/4 skip.
- 🚫 헬퍼 / 어노테이션 정의 자체에 엣지 등록 (실제 사용자 X).
- 🚫 같은 chunk_id 재호출.
- 🚫 근거 없이 등록.

---

## 완료 체크리스트

- [ ] 모든 config 키 처리.
- [ ] 4가지 시나리오 모두 시도 (직접 / 상수경유 / 헬퍼경유 / 어노테이션경유).
- [ ] 각 키의 모든 사용자 빠짐없이 등록 (시작점 있는데 등록 0 이면 빠뜨림 의심).
- [ ] 0-hit 키워드 변형 재시도 완료.
- [ ] 근거 없는 엣지 X.

전부 YES → 완료.
