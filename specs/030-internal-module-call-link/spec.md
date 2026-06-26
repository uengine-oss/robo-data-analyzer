# Feature Specification: framework 내부 모듈 호출의 정규 해소 (외부 스텁 중복 제거)

**Feature Branch**: `030-internal-module-call-link`

**Created**: 2026-06-26

**Status**: Draft

**Input**: framework 호출 분류가 분석대상 내부 모듈을 외부 맨이름 스텁으로 중복 생성. 내부 모듈 호출(특히 ProFrame C 동적호출 래퍼 `mpfm_dlcall`)을 내부 정식 노드로 해소하도록 수정.

## 배경 (왜)

분석 결과 그래프에서, 분석 대상에 **이미 존재하는 내부 모듈**이 다른 모듈에서 호출되면 **외부 스텁 노드(맨이름, EXTERNAL)로 한 번 더** 만들어져 같은 모듈이 그래프에 중복으로 나타난다.

실데이터 확정(neo4j, framework=zapamcom): 이름 `zapamcom10060`이 4개 노드로 존재 —
- 내부 `:FILE` `zapamcom.zapamcom10060` (논리명 있음, 정상)
- 내부 `:FUNCTION` `zapamcom.zapamcom10060.zapamcom10060` (엔트리, 정상)
- 🔴 외부 `:FUNCTION:EXTERNAL` `zapamcom10060` (맨이름, 논리명 없음)
- 🔴 외부 `:MODULE:EXTERNAL` `zapamcom10060` (맨이름)

원인 호출: `zapamcom10140.c:1261` `mpfm_dlcall("zapamcom10060", &io)` — ProFrame C의 동적 모듈 호출. 이 호출이 내부 모듈 `zapamcom.zapamcom10060`으로 연결되지 못하고 맨이름 외부 스텁을 새로 만든다.

근본 위치(전수조사 확정): framework 호출 분류기에서 **래퍼 호출의 대상 이름을 내부 모듈로 해소하는 경로가 키 형식 불일치로 항상 실패**한다 — 내부 모듈 색인은 모듈의 **정식 id(접두어 포함)**로 키를 잡는데 호출에서 추출한 값은 **모듈 맨이름**이라 영영 매칭되지 않는다. 반면 같은 분류기의 **직접(비-래퍼) 호출 해소 경로는 정상**(엔트리 함수 맨이름으로 내부를 정확히 찾음).

영향 범위: 래퍼(`mpfm_dlcall`) 대상 전부가 외부 스텁이 되며, 그중 **소스에도 있는 내부 모듈만** 중복으로 가시화된다(소스에 없는 진짜 외부 대상은 외부 스텁이 정상). dbms 분석은 별도 분류기를 쓰므로 무관(범위 밖).

## User Scenarios & Testing *(mandatory)*

### User Story 1 - 내부 모듈 호출이 중복 없이 한 번만 (Priority: P1)

분석 결과를 보는 사용자가, 한 모듈이 다른 내부 모듈을 호출하더라도 그 내부 모듈을 **그래프·목록에서 한 번만** 본다(내부 노드 1세트). 외부 맨이름 복제본이 생기지 않는다.

**Why this priority**: 이 기능의 본질. 사용자가 처음 발견한 문제(목록 중복)를 직접 해소.

**Independent Test**: framework 1회 분석 → 내부 모듈 이름으로 노드 조회 시 내부 노드(FILE/엔트리 FUNCTION)만 있고 동명 EXTERNAL 스텁이 0인지 확인.

**Acceptance Scenarios**:

1. **Given** 모듈 A가 분석 대상 내부 모듈 B를 호출하는 코드, **When** 분석을 실행, **Then** B는 내부 노드로만 존재하고 동명 외부 스텁 노드는 생성되지 않는다.
2. **Given** A가 래퍼(`mpfm_dlcall("B", …)`)로 B를 호출, **When** 분석을 실행, **Then** A→B 호출이 **내부 호출(internal)** 로 B의 정식 내부 노드에 연결된다.
3. **Given** A가 소스에 없는 외부 대상 X를 호출, **When** 분석을 실행, **Then** X는 기존대로 외부 스텁으로 남는다(정상 동작 보존).

### User Story 2 - 직접 호출·기존 동작 무회귀 (Priority: P2)

기존에 정상이던 호출 분류(직접 호출의 내부/외부/드롭, dbms 호출, stdlib/framework 드롭)는 그대로 동작한다.

**Why this priority**: 회귀 방지. 수정이 좁은 영역(래퍼 내부 해소)에 한정됨을 보장.

**Independent Test**: 분석 전후 internal/external/dropped 호출 집계가 래퍼-내부건 외에는 변하지 않는지 확인.

**Acceptance Scenarios**:

1. **Given** 직접 호출(래퍼 아님)로 내부 함수를 부르는 코드, **When** 분석, **Then** 종전과 동일하게 internal로 해소된다.
2. **Given** stdlib·framework 런타임 호출, **When** 분석, **Then** 종전과 동일하게 드롭된다.

### Edge Cases

- **동명 모듈이 둘 이상**(다른 패키지에 같은 short명): 호출 대상 해소가 모호 → **결정론 규칙**(호출자와 같은 패키지/모듈 우선, 그래도 다수면 정렬상 첫 후보)으로 안전 처리.
- **래퍼 인자가 내부 모듈이 아님(진짜 외부)**: 외부 스텁 유지(정상).
- **엔트리 함수가 없는 모듈**(모듈명과 같은 함수가 없음): 그 모듈의 대표 내부 노드로 결정론적으로 연결(없으면 외부로 둠).
- **죽은 코드**: 현재 항상 실패하는 엔트리 판정·미사용 색인은 제거(데드 제거).

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: framework 호출 분류는 래퍼에서 추출한 호출 대상 이름이 **분석 대상 내부 모듈과 일치하면** 그 모듈의 **내부 정식 노드(엔트리/대표 함수의 정식 id)** 로 연결(internal)해야 한다.
- **FR-002**: 내부로 해소된 호출에 대해 **외부 맨이름 스텁 노드(EXTERNAL)를 생성하지 않아야** 한다.
- **FR-003**: 소스에 없는 진짜 외부 대상은 기존대로 외부 스텁으로 남겨야 한다(정상 동작 보존).
- **FR-004**: 내부 모듈이 동명 외부 스텁으로 중복 생성되는 건수가 **0** 이어야 한다.
- **FR-005**: 직접(비-래퍼) 호출, dbms 호출, stdlib/framework 드롭 등 **기존 분류 동작은 무회귀**여야 한다.
- **FR-006**: 동명 모듈 다수 시 호출 대상 해소는 **결정론적**(같은 입력→같은 결과)이어야 한다.
- **FR-007**: 항상 실패하는 엔트리 판정식·그로 인해 비는 색인 등 **죽은 코드는 제거**한다(근본 정리).

### Key Entities *(include if feature involves data)*

- **내부 모듈(Module/FILE 노드)**: 분석 대상 소스의 모듈. 정식 id = 접두어 포함(예 `zapamcom.zapamcom10060`). 호출 대상 해소의 매칭 기준.
- **엔트리/대표 함수(FUNCTION 노드)**: 모듈을 호출했을 때 연결될 내부 노드(모듈명과 같은 함수 등).
- **호출 관계(Calls 엣지)**: caller→callee. scope=internal(내부 정식 id) / external(진짜 외부만 스텁).

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: framework 재분석 후, 분석 대상 내부 모듈 이름으로 조회 시 **동명 EXTERNAL 스텁 노드 = 0개**.
- **SC-002**: 래퍼로 호출된 내부 모듈에 대해 caller→callee **internal Calls 엣지**가 내부 정식 id로 1개 이상 존재.
- **SC-003**: 소스에 없는 외부 대상(zngmmcap50000 등)은 분석 후에도 external 스텁으로 유지(개수 보존).
- **SC-004**: 수정 전후 전체 호출 분류 집계에서 internal/external/dropped 차이가 **래퍼-내부 해소분에만** 국한(그 외 무변화).

## Assumptions

- 호출 대상의 "모듈 맨이름"은 분석 대상 내부 모듈의 short명과 동일 규칙으로 비교 가능하다(접두어 정규화로 매칭).
- ProFrame C의 모듈 호출 래퍼(`mpfm_dlcall`)는 스킬에 등록돼 있고 첫 문자열 인자가 대상 모듈명이다(기존 메커니즘 유지).
- 변경 범위는 framework 호출 분류 한정. dbms 분류기·다른 분석 단계·Neo4j 스키마/계약은 무변경(신규 라벨·관계·노드 0).
- 검증은 framework 소스(zapamcom)로 재분석해 실데이터로 수행(dbms는 범위 밖이라 미실행).
