# Feature Specification: code_rules_examples 응답구조 최소화

**Feature Branch**: `039-rules-examples-response-slim`

**Created**: 2026-07-01

**Status**: Draft

**Input**: analyzer 코드분석 3분할 중 code_rules_examples(분할③) 응답구조를 최소화한다. LLM은 "자연어 의미"만 출력하고, 구조적 사실(룰 흐름)은 응답에서 뺀다. examples를 rules 하위로 중첩해 ID 접착제를 없앤다.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - LLM이 의미만 출력하도록 응답 슬림화 (Priority: P1)

분석 파이프라인 운영자로서, code_rules_examples 프롬프트가 LLM에게 **규칙 문장 + 그 아래 시나리오**만 내게 해서, LLM이 계층 번호(`flow_id` "1-1-1")·교차참조 ID(`rule_id`)를 매기느라 헤매거나 출력이 폭주하는 것을 없애고 싶다.

**Why this priority**: 응답 폭주·토큰·ID 참조 오류의 직접 원인 제거. 나머지 스토리의 전제.

**Independent Test**: framework·dbms 대표 코드를 재분석해 응답이 `{rules:[{statement, examples:[{given,when,then,affected_tables}]}]}` 형태로만 나오고, `rule_id`/`flow_id`/`description` 필드가 응답·그래프에 0건임을 확인.

**Acceptance Scenarios**:

1. **Given** framework 함수(예: b200), **When** 재분석, **Then** RULE 노드의 `flow_id` 속성·룰 레벨 `NEXT`/`BRANCH` 엣지가 0건, EXAMPLE은 소속 RULE 하위로만 연결, `affected_tables`→`AFFECTS_TABLE` 엣지는 그대로 존재.
2. **Given** dbms 프로시저, **When** 재분석, **Then** 룰 레벨 NEXT/BRANCH 0건이되 **구문(statement) 레벨 NEXT·PARENT_OF는 불변**, 분기는 AST(ELSIF/ELSE)에서 계속 도출됨.

---

### User Story 2 - 소비자 정합 (하류 무회귀) (Priority: P1)

architect 하이브리드 인제스천이 이 슬림해진 그래프를 읽어 요구사항(UserStory)·DDD를 만들 때, 룰 흐름 힌트가 사라져도 **에러 없이 동작**하고, 남은 근거(statement·given/when/then·affected_tables)로 결과를 낸다.

**Why this priority**: 생산자 응답 변경은 곧 그래프 계약 변경 → 소비자가 옛 필드를 읽으면 조용히 NULL/에러. 동시 정합 없으면 회귀.

**Independent Test**: architect `dbms_rule_linearizer`·`bpm_context_builder`가 없어진 `flow_id`/룰 NEXT·BRANCH를 참조하지 않고, guard/branch 힌트 부재를 graceful 처리(빈 값)함을 코드·실행으로 확인.

**Acceptance Scenarios**:

1. **Given** flow_id 없는 그래프, **When** bpm_context_builder가 룰 컨텍스트 구성, **Then** `guard_rule_id`/`branch_from` 힌트는 빈 값이되 예외 없이 statement·affected_tables·coupled_domains 기반 컨텍스트를 생성.
2. **Given** 재분석된 그래프, **When** PRD 생성, **Then** Open Decisions(questions) 섹션·AFFECTS_TABLE 기반 Acceptance Test는 정상.

---

### Edge Cases

- dbms 프로시저의 분기가 statement 텍스트에만 있고 AST 라벨(ELSIF/ELSE)이 없으면? → 분기 힌트 부재로 처리(요구사항은 statement 조건으로 표현, 회귀 아님).
- 룰이 0개인 함수(단순 위임)? → rules 빈 배열, 무변경.
- 기존 그래프(옛 스키마)와 신규 재분석 혼재? → 재분석이 전제(옛 flow_id 잔재는 재분석 시 미생성).

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: code_rules_examples 응답에서 `rule_id`(rule·example)·`flow_id`(rule)·`description`(example)를 제거한다. 유지 필드 = rule의 `statement`·`coupled_domains`, example의 `given`·`when`·`then`·`affected_tables`.
- **FR-002**: examples는 소속 rule 하위로 **중첩**한다(별도 배열 + `rule_id` 접착제 폐기).
- **FR-003**: `questions` 배열은 **유지**한다(PRD Open Decisions 소비, 저빈도).
- **FR-004**: `affected_tables`(→`AFFECTS_TABLE` 엣지)는 **유지**한다(다수 테이블 시 규칙↔테이블 대응·하류 Aggregate/Command/Event 도출 근거).
- **FR-005**: 룰 레벨 `NEXT`/`BRANCH` 엣지 및 `HAS_RULE.flow_id` 속성 생성을 폐기한다. **구문(statement) 레벨 `NEXT`·`PARENT_OF`, `BpmTask.NEXT`는 불변**(공유 심볼 — 룰 레벨만 정밀 제거).
- **FR-006**: framework·dbms 두 프롬프트의 출력 스키마·지침을 동일 구조로 동기화하고, KV(prefix) 캐시 byte-identical 원칙(공통 system 불변)을 지킨다.
- **FR-007**: 폭주 안전망 유지 — 고정 필드 중첩, 자유키 dict 금지, `max_length` 캡 유지.
- **FR-008**: 소비자(architect `dbms_rule_linearizer`·`bpm_context_builder`·`rule_extractor`·`contracts`, frontend 룰흐름 렌더)를 동시 정합해 폐기 필드·엣지 참조를 제거하고 부재를 graceful 처리한다.
- **FR-009**: RULE·EXAMPLE 노드 자체, `HAS_RULE`·`HAS_EXAMPLE`·`AFFECTS_TABLE`·`HAS_QUESTION` 관계는 불변(회귀금지).

### Key Entities

- **RULE 노드**: 비즈니스 규칙. 속성 `statement`·`coupled_domains`(유지) — `flow_id`(HAS_RULE 속성) 제거.
- **EXAMPLE 노드**: given/when/then 시나리오. `description` 제거, RULE 하위 중첩. `affected_tables`→`AFFECTS_TABLE` 유지.
- **QUESTION 노드**: 모호점 플래그. 유지.
- **룰 흐름 관계(NEXT/BRANCH, 룰 레벨)**: 폐기.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 재분석 후 그래프에서 `HAS_RULE.flow_id` 속성 0건, 룰 레벨 `NEXT`/`BRANCH` 엣지 0건.
- **SC-002**: EXAMPLE의 `description` 속성 0건, LLM 응답에 `rule_id`/`flow_id` 토큰 0건.
- **SC-003**: `AFFECTS_TABLE` 엣지·`QUESTION` 노드·구문레벨 NEXT는 재분석 전후 개수 동등(무회귀).
- **SC-004**: architect 하이브리드 인제스천이 재분석 그래프로 UserStory·Aggregate/Command/Event 생성 시 런타임 에러 0.
- **SC-005**: framework·dbms 두 프롬프트 공통 system 영역 `diff` 0(KV 캐시 무손상).

## Assumptions

- 이 변경 후 **재분석이 전제** — 옛 flow_id/룰NEXT 잔재는 재생성되지 않음(그래프 초기화 또는 재인제스천).
- architect `event_storming_bridge`의 `decomposer`·`persistence`(decompose_task 경로)는 미배선(데드)이라 런타임 영향 0 — 단 코드 정합 대상.
- frontend 룰 흐름 렌더가 존재하면 함께 정리, 없으면 해당 없음.
- coupled_domains(rule)는 bpm_context_builder가 LLM 힌트로 사용(라이브) → 이번 범위 유지(별도 결정 시 후속).
