# Feature Specification: 라이브 스트림 ↔ neo4j 그래프 정합 (룰흐름 self-loop + 외부스텁 누락)

**Feature Branch**: `035-stream-rule-flow-endpoints`

**Created**: 2026-06-28

**Status**: Implemented

**Input**: 라이브 분석(스트림) 그래프가 저장본(neo4j)과 달라, 화면에서 함수 self-loop·외부호출 누락이 보이는 버그의 근본수정. 스트림이 **저장과 동일한 최종 그래프**를 보내게 한다.

## 배경 (전수 비교로 확정된 차이)

zapamcom(framework) 라이브 분석 스트림 vs neo4j 저장본을 id 중복제거 후 전수 비교한 결과, **딱 2가지**가 달랐다:

1. **룰 흐름(NEXT/BRANCH) self-loop**: 스트림이 `from.id=to.id=함수id` + rule_id 로 발신 → 프론트가 함수 self-loop 로 렌더. DB 저장(rule_flow.py)은 HAS_RULE 의 local_rule_id 로 룰 노드를 찾아 룰→룰 로 저장. (21개 self-loop)
2. **외부 호출 스텁 누락**: 스트림이 외부 CALLS 를 원시 callee_id 로만 발신 → `FUNCTION:EXTERNAL`/`MODULE:EXTERNAL` 스텁 노드·HAS_MEMBER 가 라이브에 없음. DB 저장(calls.py)은 스텁 노드를 `{module}.{name}` id 로 합성. (노드 16 + HAS_MEMBER 8 + 외부 CALLS 끝점 불일치)

(CALLS·READS·AFFECTS_TABLE 개수 차이는 id 중복제거 전 날것 셈 착시였고, 실제론 일치.)

## User Scenarios & Testing *(mandatory)*

### User Story 1 - 라이브 그래프가 저장본과 동일하다 (Priority: P1)

사용자가 코드를 라이브 분석할 때 보는 그래프(노드·관계·끝점)는, 분석 후 저장된 그래프(neo4j)와 **완전히 동일**해야 한다. 지금은 라이브에 함수 self-loop 가 보이고 외부 호출 노드가 빠져 있어, 라이브와 저장본이 다르다.

**Why this priority**: 라이브와 저장본이 다르면 사용자가 잘못된/혼란스러운 그래프를 보게 된다. "보는 것 = 저장되는 것" 이 깨진 핵심 신뢰 문제.

**Independent Test**: framework 프로젝트를 라이브 분석하고 스트림 이벤트로 그래프를 만들어, neo4j 저장본과 노드 수·관계 타입별 수·끝점 집합을 전수 비교 → 완전 일치.

**Acceptance Scenarios**:

1. **Given** 라이브 분석, **When** 룰 흐름(NEXT/BRANCH)이 전달될 때, **Then** 끝점은 서로 다른 **규칙 노드**다(함수 self-loop 아님).
2. **Given** 외부 호출이 있는 함수, **When** 그 호출이 전달될 때, **Then** 외부 스텁 노드(FUNCTION/MODULE:EXTERNAL)와 소속(HAS_MEMBER)도 함께 전달돼 호출 끝점이 실제 노드를 가리킨다.
3. **Given** 분석 완료, **When** 라이브 그래프와 neo4j 저장본을 비교한다, **Then** 노드·관계·끝점이 100% 일치(self-loop 0, 끝점없는 선 0).

### Edge Cases

- 룰 흐름 끝점(규칙)을 해소 못하면 그 흐름은 생략(잘못된 self-loop 안 만듦). 저장본엔 정상 저장되어 재로드 시 복원.
- 같은 외부 콜리를 여러 곳에서 호출 → 스텁 노드/HAS_MEMBER 는 같은 id 라 중복제거(저장 MERGE 와 동일).
- 룰흐름·외부 외 다른 관계(HAS_COLUMN·PARENT_OF·READS/WRITES 등)는 끝점이 이미 실 노드라 변경 없음.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: 룰 흐름(NEXT/BRANCH)은 끝점으로 **규칙 노드 id**를 실어 발신(함수 id 아님). 출발≠도착.
- **FR-002**: 외부 호출은 스텁 노드(FUNCTION:EXTERNAL, MODULE:EXTERNAL) + HAS_MEMBER(+PACKAGE/BELONGS_TO) + 끝점이 스텁인 CALLS 를 함께 발신한다.
- **FR-003**: 외부 스텁의 식별자·구조는 저장(upsert_calls)과 **동일 로직**으로 산출(단일출처, 드리프트 0).
- **FR-004**: 룰 흐름 끝점 미해소 시 그 관계는 생략한다(잘못된 끝점으로 발신 금지).
- **FR-005**: 저장(DB) 결과 및 룰흐름·외부 외 다른 관계의 발신은 변경하지 않는다(회귀 0).

### Key Entities

- **규칙(RULE) 노드 / 룰 흐름(NEXT/BRANCH)**: 함수 내부 업무단계와 그 순서·분기. 흐름 끝점은 규칙 노드.
- **외부 스텁(FUNCTION/MODULE:EXTERNAL) / HAS_MEMBER**: 분석 범위 밖 호출 대상의 합성 노드와 소속.
- **공유 해소(_split_calls)**: (caller, callee) → 내부/외부 분류 + 외부 스텁 id(`{module}.{name}`) 산출. 저장·발신 단일출처.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 라이브 스트림 그래프 == neo4j 저장본 — 노드 수·관계 타입별 수·끝점 집합 100% 일치.
- **SC-002**: 스트림의 self-loop 0, 끝점없는 선(허공선) 0.
- **SC-003**: 저장본(neo4j) 결과는 변경 전후 동일(저장 로직 무변경, 회귀 0).

## Assumptions

- 규칙 노드는 라이브 스트림에 이미 노드로 전달되고, HAS_RULE 가 (함수, 로컬 규칙번호)→규칙 노드 id 매핑을 제공한다.
- 외부 스텁 id 산출 로직(`{module}.{name}`)은 저장부에 있어 공유 함수로 추출 가능.
- 프론트는 룰→룰·외부스텁을 이미 올바르게 렌더한다(저장본 fetch 경로로 확인) → 프론트 변경은 이 스펙 범위가 아니다.

## 검증 결과 (Implemented)

zapamcom framework 재분석 후 스트림 이벤트 vs neo4j 전수 비교: **노드 306=306, 관계 14타입 전부 일치, self-loop 0, 허공선 0, 끝점불일치 0 — 완전일치.**
