---
name: config-tracking
description: >
  **언제 사용**: 사용자 메시지의 `카테고리:` 값이 `config` 일 때 **반드시** 이 스킬을 로드할 것.
  data 에 미매칭 설정 키(config_key_path, file_path) 가 포함됨.
  **작업 내용**: 각 config_key 를 코드에서 참조하는 **모든 함수** 에
  `(FUNCTION)-[:USES_CONFIG]->(ConfigSection)` 관계 생성.
  **트리거 키워드**: application.yml, application.properties, config_lookup_tool,
  getProperty, @Value, environment variable, 미매칭 ConfigSection, USES_CONFIG 엣지.
  **사용 금지 (Do NOT)**: 엔티티/클래스 추적은 `entity-analysis`, DDL 테이블 추적은
  `table-tracking`, SQL 키 추적은 `sql-tracking` 사용.
---

# Config Tracking Skill

미매칭된 설정 키를 코드에서 추적하여 관계를 생성한다.

## 카테고리별 처리

### category = config

각 아이템은 `{key, target_type, file_path}` 를 갖는다.

1. `config_lookup_tool(key)` 로 설정 값(value, file_path) 확인.
2. 다양한 검색어로 사용처 탐색:
   - 설정 키 전체: `bundle_search(config_key, kind="text")`
   - 키의 마지막 부분: `bundle_search("database.url", kind="text")`
   - 파일명: `bundle_search("application.yml", kind="text")`
   - 관련 키워드: `bundle_search("getProperty", kind="text")`
3. 각 검색 결과의 코드를 `bundle_get_chunk` 로 확인.
4. 설정 키를 참조하는 코드가 확인되면:
   - `graph_create_refer_to_tool("ConfigSection", config_key, evidence, source_function_id)` — USES_CONFIG 관계.

---

## 목표

미매칭된 설정 키(config_key_path) 를 참조하는 **모든 함수** 를 찾아
`(FUNCTION)-[:USES_CONFIG]->(CONFIG_SECTION)` 관계를 생성한다.

---

## 공통 규칙

- `source_function_id` 는 반드시 `bundle_search` 에서 발견한 실제 함수의 chunk_id 사용.
- 하나의 설정을 여러 함수가 참조할 수 있음 — 모두 찾아 등록.
- 검색 결과가 0건이면 검색어를 바꿔서 최소 2~3회 재시도.
- 확신이 있을 때만 등록. 근거 부족 시 생성 금지.
