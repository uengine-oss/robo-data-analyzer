# Feature Specification: 외부 호출 스텁 — 모듈·엔트리 함수 id 구분

**Feature Branch**: `031-external-stub-distinct-id`

**Created**: 2026-06-26

**Status**: Draft

**Input**: 외부 호출 스텁의 컨테이너(MODULE)와 엔트리 함수(FUNCTION)가 같은 id라 프론트에서 한 노드로 합쳐져 "외부 모듈 아래 함수가 안 보임". 내부 파일처럼 모듈 + 엔트리 함수(구분된 id) 구조로 표현되게 한다.

## 배경 (왜)

외부 호출(분석 대상 밖 프로그램/프로시저)은 **컨테이너(모듈) + 실제 호출되는 함수(엔트리)** 로 표현되는 게 정상 구조다 — 내부 파일이 `FILE + 멤버 함수`인 것과 동일. 그래야 화면(네비게이터/그래프)에서 "외부 모듈 → 그 안의 호출 함수"로 보인다.

그러나 현재 `shared/neo4j/edges/calls.py` 의 `EXTERNAL_QUERY` 는 외부 호출 1건당:
- `:FUNCTION:EXTERNAL {id: callee_id}` (엔트리 스텁)
- `:MODULE:EXTERNAL {id: callee_module}` (컨테이너)
- `(MODULE)-[:HAS_MEMBER]->(FUNCTION)`
를 만드는데, linker 가 `callee_module` 을 비워 `callee_module or callee_id` 폴백(`upsert_calls`)으로 **모듈 id = 함수 id = 호출명**(예 둘 다 `zapamcom10020`)이 된다.

노드의 정체성 키는 **id 하나**(graph_nodes.py 규칙). 같은 id 두 노드는 식별키 유일성을 위반하고, 프론트는 노드를 id 로 식별(스트림은 논리 id만 제공)하므로 모듈·함수가 **한 칸에 덮어써져 하나만 남는다**(외부 모듈만 보이고 함수 사라짐). 실측: 내부 `zapamcom10060` 은 FILE id `zapamcom.zapamcom10060` ≠ FUNCTION id `zapamcom.zapamcom10060.zapamcom10060`(구분, 정상) / 외부 `zapamcom10020` 은 MODULE·FUNCTION 둘 다 id `zapamcom10020`(동일, 문제).

→ 외부 엔트리 함수에 **내부와 동일한 패턴의 구분된 id**(`{module_id}.{name}`)를 부여하면, 모듈·함수가 별개 노드로 공존하고 화면에 "모듈 → 엔트리 함수"로 표시된다(프론트 spec 027 의 논리 id 키와도 충돌 없음 — id 가 다르므로 합쳐지지 않음).

## User Scenarios & Testing *(mandatory)*

### User Story 1 - 외부 모듈 아래 엔트리 함수가 보인다 (Priority: P1)

분석 결과를 보는 사용자가 외부 호출 모듈을 펼치면 그 안에 **실제 호출되는 엔트리 함수**가 보인다(내부 파일이 멤버 함수를 가진 것과 동일).

**Why this priority**: 외부 호출의 정상 표현(컨테이너+함수) 복원 — 사용자가 지목한 문제.

**Independent Test**: framework 재분석 후, 외부 모듈 노드와 그 HAS_MEMBER 엔트리 함수 노드가 **서로 다른 id**로 존재하고, 네비게이터에서 모듈 아래 함수가 1개 보인다.

**Acceptance Scenarios**:

1. **Given** 외부 프로그램을 호출하는 코드, **When** 분석, **Then** `:MODULE:EXTERNAL`(컨테이너)와 `:FUNCTION:EXTERNAL`(엔트리)가 **구분된 id**로 생성되고 MODULE-[:HAS_MEMBER]->FUNCTION 으로 연결된다.
2. **Given** 그 그래프를 프론트가 적재, **When** 네비게이터를 본다, **Then** 외부 모듈 아래에 엔트리 함수가 보인다(모듈만 있는 게 아니라).
3. **Given** 내부 파일, **When** 본다, **Then** 기존과 동일(무회귀) — 내부 file+함수 구조 불변.

### Edge Cases

- **같은 외부 모듈을 여러 번/여러 함수로 호출**: ProFrame 동적호출은 보통 프로그램(모듈) 단위 = 엔트리 1개. 한 모듈에 엔트리 함수 1개(HAS_MEMBER 1). 추후 여러 엔트리가 와도 id 충돌 없이 누적(MERGE dedup).
- **외부 함수 id 형식 변경의 소비처 영향**: 외부 함수 id 가 `name` → `{module}.{name}` 으로 바뀜. 외부 CALLS 대상 id 도 함께 바뀜 — 외부를 **바른 이름(bare) id 로 참조하는 소비처가 있으면 정합 필요**(plan 에서 전수확인).
- **dbms 외부 프로시저**: 동일 규칙 적용(모듈 id ≠ 함수 id).

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: 외부 호출 스텁의 컨테이너(MODULE)와 엔트리 함수(FUNCTION)는 **서로 다른 id** 를 가져야 한다(식별키 유일성 준수).
- **FR-002**: 외부 엔트리 함수 id 는 내부와 동일 패턴(`{module_id}.{name}`)으로 모듈 id 의 자식임을 드러내야 한다.
- **FR-003**: `(MODULE:EXTERNAL)-[:HAS_MEMBER]->(FUNCTION:EXTERNAL)` 연결을 유지해 화면에서 "모듈 → 엔트리 함수"로 표시되어야 한다.
- **FR-004**: caller→callee CALLS 엣지의 대상은 엔트리 함수(구분된 id)여야 한다.
- **FR-005**: 내부 노드/구조·기존 CALLS(internal)·다른 단계는 무회귀.
- **FR-006**: 외부 함수 id 형식 변경으로 깨지는 소비처가 없어야 한다(전수확인·정합).

### Key Entities *(include if feature involves data)*

- **외부 컨테이너(:MODULE:EXTERNAL)**: 호출된 외부 프로그램/모듈. id = 모듈명(예 `zapamcom10020`).
- **외부 엔트리 함수(:FUNCTION:EXTERNAL)**: 그 모듈에서 실제 호출되는 함수. id = `{module}.{name}`(예 `zapamcom10020.zapamcom10020`). HAS_MEMBER 로 모듈 소속.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 재분석 후 외부 이름별로 MODULE id ≠ FUNCTION id (같은 id 2노드 0건).
- **SC-002**: 각 외부 모듈이 HAS_MEMBER 엔트리 함수 1개 이상 보유, 프론트 네비에서 모듈 아래 함수 표시(childN ≥ 1).
- **SC-003**: 내부 파일/함수 구조·개수 무회귀.
- **SC-004**: 외부 함수 id 형식 변경에 깨진 소비처 0(빌드·동작).

## Assumptions

- 작업 = analyzer `shared/neo4j/edges/calls.py`(+필요 시 `upsert_calls`). 신규 라벨/관계 0(기존 :FUNCTION:EXTERNAL/:MODULE:EXTERNAL/HAS_MEMBER 재사용, id 만 구분).
- 프론트 spec 027(논리 id 키)은 그대로 — id 가 구분되면 자연히 모듈·함수 둘 다 표시.
- 검증 = framework(zapamcom) 재분석으로 외부 모듈+함수 표시 확인.
- 외부 함수 id 참조 소비처(architect 등)는 plan 에서 전수확인.
