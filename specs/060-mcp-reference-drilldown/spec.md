# Feature Specification: MCP 레거시 참조 목록·상세 분리

**Feature Branch**: `main`
**Created**: 2026-07-17
**Status**: In progress
**Supersedes**: spec 057의 `detail='tiered'` 응답 형상만 대체한다. 검색 선정·점수·compact 문자열 반환은 유지한다.

## 목적

`cluster_retrieve`는 AI가 검토 후보를 고르는 데 필요한 목록만 반환하고, 선택한 노드의 원문·라인·컬럼·관계는 새 `node_detail(id)` 도구로 조회한다. 첫 응답에 모든 함수 원문을 싣던 104,602바이트 기준선(19노드, 원문 77,413바이트)을 제거하면서도 RULE/EXAMPLE 근거와 ID는 잃지 않는다.

함수 `summary`와 `RULE.statement`의 책임도 분리한다. summary는 목적·주요 효과·반환 범주만 설명하고, 조건·임계값·상태별 분기 결과는 RULE만 소유한다.

## 사용자 시나리오

### US1 — 후보 목록에서 검토 대상을 고른다 (P1)

AI가 자연어 요구사항으로 `cluster_retrieve`를 호출하면 노드 ID, 이름, 라벨, 짧은 요약, 관련도, 역할, 관계 단서, 읽기/쓰기/호출, RULE/EXAMPLE을 받는다. 원문 코드와 TABLE 전체 컬럼은 받지 않는다.

**Acceptance**: 현 shopmall smoke 질의의 검색 노드·규칙·예시·테이블 근거는 기존과 건별 동일하고 `code_text`, `columns`, `search_text`, `embedding`, `element_id`는 응답 어디에도 없다.

### US2 — 선택한 노드만 무손실로 상세 조회한다 (P1)

AI가 목록의 ID로 `node_detail`을 여러 번 호출하면 매 호출마다 노드 속성, 원문 좌표, 원문 코드, 실제 관계, RULE/EXAMPLE, TABLE 컬럼을 받는다.

**Acceptance**: 코드 노드는 `file_path/start_line/end_line/code_text` 네 필드가 항상 존재하고 Neo4j 원본과 일치한다. 비코드 노드는 동일한 `source` 키에 `available=false`와 null 좌표를 명시한다.

### US3 — 잘못된 상세 요청이 조용히 성공하지 않는다 (P1)

빈 ID, 존재하지 않는 ID, 같은 ID가 중복된 오염 그래프, 코드 노드의 소스 좌표/본문 누락은 코드가 있는 성공 응답처럼 보이면 안 된다.

**Acceptance**: 안정적인 오류 코드(`EMPTY_ID`, `NODE_NOT_FOUND`, `NODE_ID_NOT_UNIQUE`, `SOURCE_INCOMPLETE`)가 JSON 텍스트로 반환된다. 비밀 연결정보와 내부 예외 전문은 노출하지 않는다.

### US4 — 요약과 업무규칙을 한 번씩만 읽는다 (P2)

사용자는 summary에서 함수의 역할을, RULE에서 조건과 상태별 결과를 읽으며 같은 규칙 문장을 두 번 읽지 않는다.

**Acceptance**: 새 분석 결과의 표본 및 전체 자동 점검에서 summary가 임계값·코드값·조건별 반환을 나열하지 않고, 기존 검색 골드 29/30 이상을 유지한다.

## 기능 요구사항

- **FR-001** `cluster_retrieve(query, database?)`는 compact JSON 문자열 한 벌만 반환한다.
- **FR-002** `detail` 인자는 제거한다. 저장소와 외부 소비자에서 호출 0임을 확인한 뒤 옛 분기를 남기지 않는다.
- **FR-003** 목록 응답은 기존 검색 선정 결과의 ID·순서·점수·role·evidence·reads/writes/calls·rules/examples를 보존한다.
- **FR-004** 목록 응답은 원문 코드, 전체 컬럼, 임베딩, 검색 전용 텍스트, Neo4j element id를 제외한다.
- **FR-005** `node_detail(node_id, database?)`는 동일한 요청별 Neo4j 선택 규칙을 사용한다.
- **FR-006** 상세는 노드의 공개 속성, `source`, 실제 입·출 관계, RULE/EXAMPLE/AFFECTS_TABLE, TABLE/COLUMN을 결정론적으로 조립한다.
- **FR-007** 상세의 관계는 Neo4j에 실제 존재하는 관계만 반환하며 type, direction, 상대 id/name/labels, 관계 공개 속성을 보존한다. 자기 CALLS도 실제 존재하면 한 번만 반환한다.
- **FR-008** embedding, search_text, graph_owner 및 DB 내부 element id는 상세에서도 제외한다.
- **FR-009** summary 프롬프트의 공통 계약은 목적·주요 효과·반환 범주만 허용하고 조건·임계값·코드값·상태별 분기 결과의 나열을 금지한다. framework 기본/축소 폴백과 dbms 배치가 같은 정책을 공유한다.
- **FR-010** 오류와 부분 소스 누락은 명시적으로 표면화하며 빈 성공 객체로 폴백하지 않는다.

## 비기능 요구사항

- 검색 코어와 골드 선정은 변경하지 않는다.
- 첫 응답 크기는 현 기준선보다 작아야 하며 원문 코드 바이트는 0이어야 한다.
- 도구 목록, 호출, 오류를 실제 streamable HTTP에서 검증한다.
- 프롬프트 공통 prefix와 hash 계약을 갱신하고 byte-identical 검사를 통과한다.

## 범위 밖

- 검색 랭킹의 노이즈 6→4 조정, test DB 복구, Architect UI는 각 후속 task/Architect spec이 소유한다.
- 역사 spec 057의 기록을 개작하지 않는다.
