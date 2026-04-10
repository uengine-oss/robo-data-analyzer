---
name: config-tracking
description: >
  미매칭된 설정(Config) 키를 추적하여, 해당 설정을 참조하는 함수를 찾고
  USES_CONFIG 관계를 생성한다.
  category가 "config"인 미해결 아이템에 사용.
allowed-tools: config_lookup_tool, bundle_search_tool, bundle_get_chunk_tool, graph_create_refer_to_tool
---

# Config Tracking Skill

미매칭된 설정 키를 코드에서 추적하여 관계를 생성한다.

## 목표

미매칭된 설정 키(config_key_path)를 참조하는 **모든 함수**를 찾아
`(FUNCTION)-[:USES_CONFIG]->(CONFIG_SECTION)` 관계를 생성한다.

---

## 절차

각 미매칭 Config 아이템에 대해:

### 1. 설정 내용 확인
- `config_lookup_tool(config_key)` → 설정 값 확인 (value, file_path)

### 2. 사용처 검색
다양한 검색어로 반드시 재시도:
- 설정 키 전체: `bundle_search(config_key, kind="text")`
- 키의 마지막 부분: `bundle_search("database.url", kind="text")`
- 파일명: `bundle_search("application.yml", kind="text")`
- 관련 키워드: `bundle_search("getProperty", kind="text")`

### 3. 코드 확인 + 관계 등록
- 각 검색 결과의 코드를 `bundle_get_chunk`로 확인
- 설정 키를 참조하는 코드가 확인되면:
  - `graph_create_refer_to_tool("ConfigSection", config_key, evidence, source_function_id)` — USES_CONFIG 관계

---

## 공통 규칙

- source_function_id는 반드시 bundle_search에서 발견한 실제 함수의 chunk_id 사용
- 하나의 설정을 여러 함수가 참조할 수 있음 — 모두 찾아 등록
- 검색 결과가 0건이면 검색어를 바꿔서 최소 2~3회 재시도
- 확신이 있을 때만 등록. 근거 부족 시 생성 금지.
