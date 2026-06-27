# Feature Specification: 스트림 node_event 멀티라벨 — 역할(stereotype) 라이브 표시

**Feature Branch**: `refactor`

**Created**: 2026-06-27

**Status**: Draft

**Input**: 실시간 분석 중 노드의 역할(stereotype)이 화면에 안 보이고 분석이 끝나 전체 그래프를 다시 불러와야 보임. 원인 = `node_event`가 라벨을 **단일**(`label`=antlr 타입)로만 보냄. → node_event가 **전체 라벨 배열**(타입+stereotype)을 싣게 해 역할이 라이브로 보이게.

## 배경 (왜)

- `emit_nodes`(graph_stream.py)가 `node_event {label, id, props}`로 **라벨 1개**(`_node_label`=antlr 타입)만 발신. stereotype은 `props`에 속성으로만 옴.
- 프론트 필터의 "역할" 섹션·캔버스 멀티라벨은 **라벨 기반**이라, 스트림에 stereotype 라벨이 없으면 분석 종료 후 Neo4j 전체 로드(`getGraphData`) 시에만 보인다.
- stereotype은 함수=step6(analyze_code, update_summaries 직후 emit 시점에 이미 set), 그릇=step7(summarize_module)에서 정해짐. step6 emit엔 stereotype이 이미 있으나 **단일 label만 보냄**. step7은 **emit 자체가 없음**(모듈 역할 미스트림).

## User Scenarios & Testing *(mandatory)*

### User Story 1 - 함수 역할이 분석 중 라이브로 라벨에 실림 (Priority: P1)

함수가 분석되는 즉시(step6) 그 `node_event`가 `[antlr타입, stereotype]` 라벨 배열을 실어, 프론트가 라이브로 역할 라벨을 받는다.

**Why this priority**: 사용자 요구 — 분석 끝까지 기다리지 않고 역할이 보이게.

**Independent Test**: 분석 스트림을 캡처해 함수 node_event의 `labels`에 `[FUNCTION, <stereotype>]`이 있는지 확인.

**Acceptance Scenarios**:
1. **Given** step6에서 stereotype="Command"로 분석된 함수, **When** `_emit_code_graph`, **Then** node_event `labels`=`[FUNCTION, Command]`.
2. **Given** stereotype 미판정 함수, **When** emit, **Then** `labels`=`[FUNCTION]`(타입만).

### User Story 2 - 그릇(모듈) 역할도 라이브로 (Priority: P2)

모듈(FILE/CLASS) 요약(step7)에서 stereotype이 정해지면 그 즉시 node_event를 다시 보내 역할 라벨이 라이브로 반영된다.

**Why this priority**: FILE→Service 등 그릇 역할도 라이브. 현재 step7은 emit이 없어 전체 로드까지 안 보임.

**Independent Test**: step7 후 모듈 node_event의 `labels`에 stereotype 포함 확인.

**Acceptance Scenarios**:
1. **Given** step7 summarize_module이 FILE stereotype="Service" 부여, **When** 적재, **Then** 모듈 node_event `labels`=`[FILE, Service]` 재발신.

### Edge Cases

- stereotype 없는 노드(테이블/컬럼/변수/구문) → `labels`=`[타입]`(단일, 무해).
- step2(load_ast) 시점 노드는 stereotype 미set → `labels`=`[타입]`. step6/step7 재emit이 멀티라벨로 갱신(프론트가 같은 id로 덮어씀).
- `label`(단일) 필드는 **유지**(back-compat) = `labels[0]`. 신규 `labels` 배열을 추가하는 additive 변경.

## Requirements *(mandatory)*

- **FR-001**: `node_event`는 `labels`(string[]) 필드를 포함해야 한다 = `[antlr타입] + (stereotype 있으면 그 값)`.
- **FR-002**: 기존 `label`(string) 필드는 유지(= `labels[0]`, 타입) — back-compat.
- **FR-003**: step6 함수 emit은 stereotype이 set된 상태라 `labels`에 stereotype 포함.
- **FR-004**: step7 summarize_module이 모듈 적재 후 **emit_nodes를 호출**해 모듈 역할 라벨을 라이브 발신.
- **FR-005**: stereotype 없는 노드는 `labels`=`[타입]`.
- **FR-006**: `labels` 순서 = `labels[0]`=타입(위치 계약 spec 033/035 일관).

## Success Criteria *(mandatory)*

- **SC-001**: 분석 스트림에서 stereotype 보유 함수 node_event 100%가 `labels`에 stereotype 포함.
- **SC-002**: 모듈(FILE 등) node_event도 step7에서 stereotype 라벨 포함해 재발신.
- **SC-003**: `labels[0]`=타입(위치 일관). `label` 필드 불변(back-compat).
- **SC-004**: stereotype 없는 노드 회귀 0(`labels`=[타입]).

## Assumptions

- 프론트는 후속 spec(036)에서 `labels`를 소비(없으면 `[label]` 폴백) — 이 spec은 생산자측.
- 스트림 계약(node_event)에 `labels` 추가 = contract 변경 → 소비처(frontend) 정합 필요(speckit-analyze 대상).
- gateway는 패스스루(무변형)라 영향 없음.
