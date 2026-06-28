# Feature Specification: stereotype 라벨 네임스페이스 분리 (UPPER)

**Feature Branch**: `036-stereotype-label-namespace`

**Created**: 2026-06-28

**Status**: Draft

**Input**: User description: "analyzer가 부여하는 stereotype 라벨(:Command 등)이 robo-architect의 DDD/CQRS 라벨과 충돌해, architect 인제스천의 라벨기반 삭제가 analyzer 함수 노드를 지워 RULE이 고아가 된다. 라벨을 대문자로 통일해 충돌을 없애되 프론트·소비처 영향을 최소화하라."

## 배경 — 확정된 결함 (zapamcom 실측 2026-06-28)

analyzer는 spec 032/033에 따라 노드에 `[antlr타입, stereotype]` 멀티라벨을 부여한다. stereotype 값은 `shared/lang/ontology_roles.yaml` 풀(PascalCase: `Command`, `Query`, `Validator`, `ApplicationService` …)이며 `shared/neo4j/nodes/stereotype_label.py`가 그대로 Neo4j **라벨**로 SET 한다.

Neo4j 라벨은 전역 단일 네임스페이스다. robo-architect는 **같은 DB**에서 DDD/CQRS 모델 라벨로 `Command`·`Event`·`Aggregate` 등을 1급으로 쓰고, ES 인제스천(`event_storming_bridge/persistence.py` `clear_promoted_nodes`·`_WIPE_LABELS`)이 재생성 시 **라벨 기반 `DETACH DELETE`** 로 자기 모델을 비운다. `_WIPE_LABELS`에 `Command`가 포함돼, analyzer가 `:Command` 라벨을 붙인 **쓰기 함수**(b200/b205/b210/b400/b410/b420·e800/d500/d510 — 9개)가 함께 삭제됐다.

**실측 결과**: analysis.json은 rule 73·has_rule 73(정상)인데 Neo4j HAS_RULE은 57만 영속 → 16 RULE이 소속 함수와 끊긴 "고아". 삭제된 함수는 전부 stereotype=`Command`였고, 살아남은 함수는 다른 stereotype(Query/Validator/ApplicationService/Adapter/Helper)이었다. `Command`만 `_WIPE_LABELS`와 정확히 겹친다.

> ⚠️ architect 삭제가 실행된 정확한 시점은 직접 목격하지 못했고(로그 부재), 데이터·코드·정황 일치에 기반한 **강한 추론**이다. 단, 라벨 충돌 자체는 양쪽 코드로 증명됐고 본 수정은 그 충돌 표면을 제거하므로 트리거 확정 여부와 무관하게 유효하다.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - stereotype 라벨 충돌로 인한 노드·룰 유실 제거 (Priority: P1)

분석을 마친 뒤 robo-architect로 설계 작업(ES 인제스천 재생성)을 하더라도, analyzer가 만든 함수 노드와 그 비즈니스 규칙(RULE/EXAMPLE) 연결이 보존되어야 한다.

**Why this priority**: 현재는 architect 사용 한 번에 analyzer 분석 결과의 일부(쓰기 함수 + 그 규칙)가 조용히 사라진다. 데이터 신뢰성의 근간 결함이며, "고아 RULE"은 소비처(프론트·architect)가 영영 못 보는 유실이다.

**Independent Test**: 빈 DB에 analyzer CLI 분석 → 함수 노드 N개·HAS_RULE M개 확인 → architect ES 인제스천(또는 동등한 `_WIPE_LABELS` DETACH DELETE) 1회 실행 → analyzer 함수 노드 N개·HAS_RULE M개가 **그대로** 남는지 확인.

**Acceptance Scenarios**:

1. **Given** analyzer가 stereotype을 부여한 함수 노드들이 영속된 상태, **When** architect ES 인제스천이 `:Command`/`:Event`/`:Aggregate` 라벨을 DETACH DELETE 하는 재생성을 실행, **Then** analyzer가 만든 함수 노드와 HAS_RULE/HAS_MEMBER/AFFECTS_TABLE 연결이 한 건도 삭제되지 않는다.
2. **Given** zapamcom 재분석, **When** 그래프를 점검, **Then** 고아 RULE(소속 함수와 HAS_RULE 없는 RULE) = 0.

### User Story 2 - 소비처(프론트·architect·catalog) 표시·읽기 무중단 (Priority: P2)

라벨 표기를 바꿔도 노드 상세의 "역할" 한글 표시, architect의 stereotype 읽기, 색칠·트리가 그대로 동작해야 한다.

**Why this priority**: 충돌만 없애려다 소비처를 깨면 회귀다. 영향이 좁고 통제됨을 보장해야 안전하게 적용한다.

**Independent Test**: 프론트 노드패널에서 역할이 "명령 (…)" 형태로 정상 표시되는지, architect module_retriever가 stereotype을 정상 반환하는지, 그래프 색·네비 트리가 정상인지 확인.

**Acceptance Scenarios**:

1. **Given** stereotype 라벨이 UPPER로 바뀐 노드, **When** 프론트 노드 상세를 열면, **Then** "역할" 행이 기존과 동일한 한글 라벨로 표시된다(원문 영문 노출·빈칸 없음).
2. **Given** architect module_retriever가 노드의 stereotype을 읽을 때, **When** 라벨이 UPPER로 바뀐 뒤, **Then** 반환되는 stereotype 값이 변경 전과 동일하다(속성 기반 읽기 무영향).

### Edge Cases

- 외부 호출 stub의 `EXTERNAL` 마커는 stereotype이 아니므로 변경 대상에서 제외(이미 UPPER).
- 한 노드에 stereotype이 없을 때(루틴 외 노드): 역할 행 미표시 — 기존과 동일.
- DB에 잔존하는 옛 표기 노드(`Rule`(Pascal) 등 과거 데이터): 본 스펙 범위 밖(재분석 시 갱신), 별도 정리.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: analyzer는 Neo4j에 stereotype을 **라벨**로 부여할 때 값을 **UPPER_SNAKE** 표기로 변환하여 SET 해야 한다(예: `Command` → `COMMAND`, `ApplicationService` → `APPLICATION_SERVICE`, `DataMutator` → `DATA_MUTATOR`). antlr 타입 라벨(`GLOBAL_VARIABLE` 등)과 표기 일관 — clarify 확정 2026-06-28.
- **FR-002**: `shared/lang/ontology_roles.yaml` 풀 값과 노드의 stereotype **속성** 값은 PascalCase 그대로 유지한다(LLM 검증·architect 속성 읽기 무중단).
- **FR-003**: 변환된 stereotype 라벨은 robo-architect의 DDD/CQRS 라벨 집합(`Command`/`Event`/`Aggregate`/`Policy`/`ValueObject` 등, PascalCase)과 **어떤 표기로도 일치하지 않아야** 한다.
- **FR-004**: 프론트 노드 상세의 "역할" 표시는 라벨 표기 변경 후에도 동일한 한글 라벨을 보여야 한다(역할 표시를 PascalCase 속성 기반으로 우선 조회하거나, 표시 맵 키를 UPPER로 동반 갱신).
- **FR-005**: 색칠(`colorLabelOf`)·네비 트리·kind 판별 등 위치/대문자정규화 기반 소비처는 변경 없이 동작해야 한다.
- **FR-006**: `navigator.ts`의 역할 필터 리스트는 현재 `ontology_roles.yaml` 풀과 이미 불일치(stale)하므로, 본 변경과 함께 현행 풀(UPPER)에 맞게 정정한다.

### Key Entities *(include if feature involves data)*

- **stereotype 라벨**: 노드의 2번째 라벨(`labels[1:]`). 역할 식별·색칠·필터·쿼리용. 본 스펙에서 UPPER로 통일.
- **stereotype 속성**: 노드의 `stereotype` 프로퍼티. 사람이 읽는 PascalCase 값. 무중단 유지(architect·프론트 폴백이 읽음).

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: zapamcom 재분석 후 고아 RULE(HAS_RULE 없는 RULE) = **0**, 함수 노드 수 = AST 함수 정의 수와 일치(현재 38 중 영속 29 → 38 전부).
- **SC-002**: analyzer 분석 결과가 영속된 DB에서 `_WIPE_LABELS` 기반 DETACH DELETE를 실행해도 analyzer 함수 노드·HAS_RULE/HAS_MEMBER/AFFECTS_TABLE 삭제 = **0건**.
- **SC-003**: 프론트 노드 상세 "역할" 한글 표시가 변경 전후 동일(회귀 0).
- **SC-004**: architect module_retriever가 반환하는 stereotype 값이 변경 전후 동일.

## Assumptions

- robo-architect는 DDD/CQRS 라벨을 PascalCase로 유지한다(반대로 architect가 UPPER로 바꾸면 다시 충돌 가능 — 그 경우 접두사 네임스페이스 필요).
- architect가 stereotype을 읽는 경로는 **속성**(`module_retriever` `f.stereotype`)이며 라벨이 아니다(전수 확인됨, 2026-06-28).
- catalog는 stereotype을 소비하지 않는다(전수 확인됨).
- 본 스펙은 analyzer(생산자)와 robo-data-frontend(소비자) 범위. architect 측 보강(삭제를 자기 표식 기반으로 = 충돌 무관 이중 안전판)은 **별건**으로 robo-architect spec 044("analyzer↔architect 그래프 계약 정합") 영역에서 다룬다(교차 계약 알람).
- 전체 작업(본 스펙 + 프롬프트 하드닝)이 끝나면 analyzer CLI 재분석 1회 → 그래프 vs 원문 품질 검증(principles §6)으로 마무리한다.
