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

미매칭 설정 키를 코드에서 추적해 **USES_CONFIG** 관계 생성.

> 공통 원칙 (융합 검색 / 점진적 강화 / 메모리 / 누락 0 / 환각 0) 은 `prompts/linking/system.md` 참조. 이 SKILL 은 **카테고리 고유 사항** 만 명시.

---

# ⚠ 절대 미션 — 전달된 모든 설정 키 × 모든 참조처 ⚠

> **전달된 모든 config 키에 대해, 코드에서 그 키를 참조하는 *모든* 함수를 빠짐없이 찾아 `USES_CONFIG` 등록. 단 1건이라도 누락되면 실패.**

| ❶ 완전성 | ❷ 효율성 (단건 스트리밍) | ❸ 정확성 |
| --- | --- | --- |
| config 키 1건도 빠뜨림 X. 한 키 참조처 1개 찾고 멈춤 X. **모든 참조 함수** 등록. 메모리 ③ = 0 까지. | **확인 → 쓰기 → 메모리 → 다음** 반복. 청크 본문은 한 번에 1개만 컨텍스트에. 묶음 `bundle_get_chunk` 절대 금지 (system.md §3.3). | 본문에 키 인용 가능한 함수만. 추측 등록 X. |

---

## 1. 입력 데이터

각 아이템: `{key, target_type, file_path}`.

먼저 `config_lookup_tool(key)` → 설정 값 / file_path 확보.

---

## 2. 4-trail 시작 키워드 (이 카테고리 고유)

system.md 의 4-trail 적용 시 시작 키워드:

| Trail | 시작 검색어 |
| --- | --- |
| **T1. 직접** | `bundle_search(<key 전체>, kind="text")` — `getProperty("<KEY>")` / `System.getenv("<KEY>")` 형태. **보강**: 키 마지막 segment (`<LAST>`) 로도 검색 후 결과 합치기. |
| **T2. 변수/상수 경유** | `static final String <CONST> = "<KEY>"` 발견 시 `bundle_search(<CONST>, kind="text")`. |
| **T3. 헬퍼 (ConfigHelper / Properties) 경유** | `bundle_search(<HELPER>, kind="text")` — `<HELPER>.get("<KEY>")` 형태. |
| **T4. 어노테이션 (`@Value` / `@ConfigurationProperties`) 경유** | `bundle_search("@Value", kind="text")` 또는 `bundle_search("@ConfigurationProperties", kind="text")` — 어노테이션 값이 `${<KEY>}` / `prefix="<KEY>"` 인지 확인. |

---

## 3. 등록 엣지

```text
graph_create_refer_to_tool(
  target_type="ConfigSection",
  target_key="<KEY>",
  evidence="<호출 라인 / 어노테이션 인용>",
  source_function_id="<callerFunc>"   ← caller
)
```

**등록 대상 결정** (어노테이션 케이스):

- `@Value` 가 필드: 해당 클래스의 메서드 (또는 생성자) 가 caller — 그 메서드 chunk_id 에 등록.
- `@ConfigurationProperties` 가 클래스: 클래스 자체 또는 그 인스턴스 사용하는 메서드에 등록.

---

## 4. 카테고리 특유 안티패턴

- 🚫 헬퍼 / 어노테이션 정의 자체에 USES_CONFIG 등록 — 실제 사용자 (caller) 가 등록 대상.
- 🚫 키 마지막 segment 만 일치하는 다른 키 등록 — 전체 키 일치 확인.

---

## 5. 완료 체크리스트 (이 카테고리 고유)

- [ ] 모든 config 키 처리.
- [ ] 각 키의 모든 사용자 빠짐없이 등록 (시작점 있는데 등록 0 이면 빠뜨림 의심).
- [ ] 헬퍼 / 어노테이션 정의 자체에 등록 X.

전부 YES → 완료.
