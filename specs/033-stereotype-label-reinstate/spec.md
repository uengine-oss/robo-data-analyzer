# Feature Specification: 역할(stereotype) 노드 라벨 재부여 + 위치기반 소비 계약

**Feature Branch**: `refactor`

**Created**: 2026-06-27

**Status**: Draft

**Input**: 사용자 최종결정 — 역할(stereotype: Service/Command/Query/Adapter/Validator/…)을 노드 **라벨**로 부여한다. 단 소비처는 **하드코딩 역할집합 없이 "위치"로** 종류와 역할을 구분한다(`labels[0]`=antlr 타입, `labels[1:]`=stereotype).

## 배경 (왜)

- 노드 라벨 = antlr 타입 단일(spec 019), stereotype = 속성(spec 020)이 현 상태.
- spec 032가 stereotype을 라벨로 올렸다가(`d61f240`) **유지보수 우려로 철회**(`607a3f9`, SUPERSEDED). 철회 근거 = "멀티레이블이 되면 소비처가 종류/역할을 매번 분류해야 하고, 하드코딩 역할집합(드리프트) 또는 Neo4j 라벨순서 의존(비보장)을 강요한다."
- **재결정(이 spec, 032 supersede)**: 그 우려는 **이 파이프라인의 라벨 생성순서로 해소된다.** analyzer는 노드를 step1~4(DDL/AST/링킹)에서 **antlr 타입 라벨로 먼저** 생성하고, stereotype은 step6(코드분석)에서 **나중에** 부여한다. Neo4j `labels()`는 라벨 토큰 생성순(= 처음 등록순)으로 반환하므로 **`labels[0]`은 항상 자기 antlr 타입, `labels[1:]`이 stereotype**이 된다(실증: 마이그레이션 시 전부 `[FUNCTION,Command]`·`[FILE,Service]`).
- → 따라서 소비처는 **하드코딩 stereotype 집합 불필요**: 위치(`labels[0]` vs `labels[1:]`)로 구분 = 드리프트 0. 라벨의 장점(필터/색상 일급, `MATCH (:Command)`)을 살리면서 철회 사유를 제거한다.

## 무중단 전략 (회귀 0)

- **이번(033, 생산자측)**: stereotype을 **라벨로 부여** + 속성도 **유지**(additive). 기존 속성 소비처(architect `module_retriever`, 프론트 노드패널)는 그대로 동작.
- 후속: 프론트 소비처를 위치기반 라벨로 이관(별도 frontend spec) → 그 후 stereotype 속성 제거(analyzer 후속 spec).

## User Scenarios & Testing *(mandatory)*

### User Story 1 - 역할이 노드 라벨로 존재, 타입은 항상 첫 라벨 (Priority: P1)

분석 후 그래프에서 stereotype을 가진 노드는 그 역할을 **라벨**로도 갖는다(예: `(:FUNCTION:Command)`, `(:FILE:Service)`). 그리고 **첫 라벨(`labels[0]`)은 항상 그 노드의 antlr 타입**이라 소비처가 위치로 종류/역할을 구분할 수 있다.

**Why this priority**: 역할 일급 시각화(필터/색상/패널 분리)의 생산자측 전제. 이게 없으면 프론트 위치기반 UI 작업이 성립 불가.

**Independent Test**: framework 재분석(또는 현재 그래프 마이그레이션) 후, stereotype 보유 노드의 `labels(n)`에서 `labels[0]`=antlr 타입, 나머지에 stereotype 포함을 Neo4j 직접 쿼리로 확인.

**Acceptance Scenarios**:

1. **Given** 함수 노드 stereotype="Command", **When** 분석 적재, **Then** `labels(n)` = `[FUNCTION, Command]`(타입 먼저).
2. **Given** FILE 노드 stereotype="Service", **When** 적재, **Then** `labels(n)` = `[FILE, Service]`.
3. **Given** stereotype 빈값(미판정), **When** 적재, **Then** antlr 타입 라벨만(역할 라벨 미부여).
4. **Given** stereotype 속성 소비처(아직 이관 전), **When** 조회, **Then** 속성도 그대로라 동작(무중단).

### User Story 2 - 현재 그래프 즉시 활용 (Priority: P2)

재분석(~4분) 없이도, stereotype 속성을 이미 가진 현재 그래프에 마이그레이션 쿼리로 라벨을 부여해 즉시 검증·활용할 수 있다.

**Why this priority**: 빠른 검증 루프. 코드 변경(영구)과 별개로 현 데이터에서 위치기반 소비를 바로 확인.

**Independent Test**: distinct stereotype별 `MATCH (n) WHERE n.stereotype=$v SET n:`$v`` 마이그레이션 후 `labels(n)` 확인.

**Acceptance Scenarios**:

1. **Given** stereotype 속성 보유 그래프, **When** 마이그레이션 실행, **Then** 보유 노드 100%가 그 stereotype을 라벨로 가지며 `labels[0]`=타입 불변.

### Edge Cases

- stereotype 값 = `coerce_stereotype`(ontology_roles.yaml 풀) 통제값 → 식별자 형태라 라벨 안전. 백틱(``:`X` ``)으로 추가 방어.
- dbms 루틴도 stereotype 보유 → 라벨 부여. dbms 구문 노드(SELECT/IF)는 stereotype 없음 → 타입만(정상).
- 외부 stub(`:FUNCTION:EXTERNAL`)은 stereotype 없음 → 역할 라벨 미부여(정상).
- **라벨 순서 caveat(솔직)**: Neo4j는 라벨 순서를 "계약"으로 보장하지 않음(토큰 id 순서). 단 이 파이프라인은 타입을 먼저 등록하므로 실증상 안 깨짐. 최악도 노드패널 표시 1회 swap(경미, 데이터/로직 손상 없음).

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: stereotype 보유 노드(code node·module)는 그 stereotype을 **노드 라벨**로 가져야 한다.
- **FR-002**: stereotype 값별로 묶어 `SET node:`<값>`` 부여(Cypher 동적 라벨 불가 → 값별 배치). 값은 검증풀 통제값 + 백틱 방어.
- **FR-003**: 라벨 부여는 antlr 타입 라벨 **이후** 시점(step6 적재)에 일어나, `labels[0]`이 antlr 타입으로 유지되어야 한다.
- **FR-004**: **무중단** — stereotype 속성은 이번 단계에서 유지(소비처 이관 후 별도 spec에서 제거).
- **FR-005**: 빈 stereotype은 라벨 부여 안 함.
- **FR-006**: framework·dbms 공통(양쪽 풀). 외부 stub·구문 노드는 stereotype 없으면 미부여.
- **FR-007**: 소비처 정합 계약은 **위치기반**(`labels[0]`=종류, `labels[1:]`=역할). 하드코딩 stereotype 집합을 생산물/계약에 포함하지 않는다.

### Key Entities

- **stereotype(역할)**: LLM이 본문 동작 판단으로 선택한 전략무관 분류값(`ontology_roles.yaml` 풀). 노드의 "무엇을 하는가"(Service/Command/…). 이번부터 라벨 + 속성 병행.
- **antlr 타입 라벨**: 노드의 "무엇인가"(FILE/FUNCTION/CLASS/…). 항상 `labels[0]`.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: stereotype 보유 노드 100%가 그 stereotype을 라벨로 가진다.
- **SC-002**: 보유 노드 100%에서 `labels[0]`=antlr 타입(타입 먼저).
- **SC-003**: 기존 stereotype 속성 소비처 회귀 0(속성 유지).
- **SC-004**: 라벨 부여로 식별 id·기존 관계 불변.

## Assumptions

- stereotype 값은 `coerce_stereotype`로 풀 검증됨 → 라벨 안전.
- 현재 그래프(stereotype 속성 보유)는 마이그레이션 쿼리로 즉시 라벨 부여 가능. 코드 변경은 향후 분석에 영구 적용.
- 생산자측 라벨 부여 코드는 철회된 `d61f240`(`shared/neo4j/nodes/stereotype_label.py` + code_node/module 호출)과 동일 — 그 코드를 복원·재적용한다.
- 소비처(프론트·architect·catalog) 위치기반 이관·속성 제거는 후속 spec(별개 capability).
- **spec 032를 supersede**: 032의 "철회·속성 유지" 결론을 이 spec이 대체한다(라벨순서 우려를 파이프라인 생성순으로 해소).
