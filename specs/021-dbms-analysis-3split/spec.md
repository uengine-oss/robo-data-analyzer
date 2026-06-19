# Feature Specification: DBMS 함수분석 3분할 재설계

**Feature Branch**: `021-dbms-analysis-3split`

**Created**: 2026-06-19

**Status**: Draft

**Input**: DBMS step6 함수분석을 전략 무관 "완벽한 3분할"(①분석/요약 ②테이블 ③비즈니스로직)로 재설계. 현 function_batch 1콜 합본이 LLM 응답 폭주(max token death)의 원인 → 3분할 분리로 근본 차단. 테이블 분할은 DML 노드만. 미분석 스킵·안 쓰는 속성 정리 포함.

## 배경 (사실 — 코드/git 확인)

- **의도**: 모든 분석은 전략 무관 **3분할** — ①분석/요약 ②테이블 ③비즈니스로직. framework 경로는 3분할 유지(`three_split_llm_fanout`).
- **이탈**: dbms는 `618943c`(2026-06-05 "step6 배치 재설계")에서 3분할 프롬프트(function_summary/function_table_insights/function_rules_examples) 삭제 → `function_batch` **1콜 합본**(`NodeAnalysis`: nodes[] → 각 노드에 rules[]/examples[](→affected_tables[])/questions[]/table_insights[](→columns[]))으로 변경.
- **결과 = 폭주**: 이 깊은 중첩 출력구조가 `openai.LengthFinishReasonError`(completion_tokens=30000 상한 도달) 직접 원인. 입력 작아도(3605tok) 출력 폭주. [[principles]] §4 = "구조화 출력 폭주 = 스키마 평탄화로 근본해결, max_tokens는 답 아님".

## User Scenarios & Testing *(mandatory)*

### User Story 1 - 안정적으로 끝나는 분석 (폭주 0) (Priority: P1)

사용자가 DBMS 소스를 분석하면, LLM이 중간에 토큰 상한으로 죽지 않고 모든 노드 분석을 끝까지 완료한다.

**Why this priority**: 현재 폭주로 특정 배치가 죽어 그 노드들의 분석이 누락됨(데이터 결손). 분석의 신뢰성 자체가 깨지는 최우선 결함.

**Independent Test**: RWIS dbms 재분석 1회 → `LengthFinishReasonError`(error.log) 0건, 모든 노드에 summary 채워짐.

**Acceptance Scenarios**:

1. **Given** 중첩이 깊던 프로시저, **When** 재분석, **Then** 해당 노드 분석이 토큰 상한 없이 정상 완료(요약·규칙 채워짐).
2. **Given** 전체 RWIS 9파일, **When** 재분석, **Then** LLM length-error 0건 + 누락 노드 0.

---

### User Story 2 - 전략 무관 3분할 분석 결과 (Priority: P1)

각 노드가 ①요약/식별 ②테이블 인사이트 ③비즈니스 규칙의 **세 관심사**로 분석된다(한 콜이 모든 걸 쏟아내지 않음). dbms도 framework와 동일한 3분할.

**Why this priority**: 합본 구조가 폭주의 구조적 뿌리. 3분할 분리가 폭주 해결이자 framework와의 일관성 회복.

**Independent Test**: 재분석 후 노드의 summary·table_insights·rules가 각 관심사로 분리 생성됨을 그래프/산출물에서 확인.

**Acceptance Scenarios**:

1. **Given** DML 노드(INSERT/SELECT…), **When** 분석, **Then** 요약·테이블 인사이트·규칙이 각각 생성.
2. **Given** 비-DML 노드(IF/LOOP/대입/COMMIT), **When** 분석, **Then** **테이블 인사이트 분할은 호출되지 않음**(요약·규칙만 해당 시).

---

### User Story 3 - 테이블 분할 = DML 노드만 (Priority: P1)

테이블 인사이트 분할은 **테이블을 실제로 건드리는 DML 노드**(SELECT/INSERT/UPDATE/DELETE/MERGE)에만 적용된다. 비-DML 노드는 테이블 분할 입력에서 제외.

**Why this priority**: 테이블 무관 노드를 테이블 분할에 넣는 건 낭비·노이즈·환각 위험. 사용자 명시 요구.

**Independent Test**: 재분석 로그에서 테이블 분할 입력 노드 집합 = DML 노드 집합과 일치(비-DML 0).

**Acceptance Scenarios**:

1. **Given** IF/대입/COMMIT 노드, **When** 분석, **Then** 테이블 분할 LLM 입력에 미포함.
2. **Given** R/W가 결정론으로 잡힌 DML 노드, **When** 분석, **Then** 그 테이블에 대한 인사이트 생성.

---

### User Story 4 - 분석 불필요 노드 스킵 (Priority: P2)

빈 블록·구조 노드(DECLARE/TRY/TRIGGER_BLOCK)·내용 없는 trivial 노드는 LLM 분석에서 제외한다. 단 **그래프 노드 자체는 보존**(구조 누락 0).

**Why this priority**: 불필요한 LLM 콜은 비용·시간·노이즈("빈 줄/빈 함수 본문" 요약 남발). 단 그래프 골격은 유지돼야.

**Independent Test**: 재분석 시 스킵 대상 노드는 LLM 콜 0이지만 Neo4j에 노드로 존재(라벨·구조 엣지 유지).

**Acceptance Scenarios**:

1. **Given** 빈 블록/구조 노드, **When** 분석, **Then** LLM 콜 없음 + 그래프 노드는 존재.
2. **Given** 스킵된 부모의 자식 흐름, **When** 분석, **Then** PARENT_OF/NEXT 등 구조 엣지 누락 0.

---

### User Story 5 - 안 쓰는 노드 속성 0 (Priority: P2)

노드에 저장되거나 소비되지 않는 속성은 남지 않는다(데드 폐기). table_insights는 실제 소비처(Table/Column 설명 분배)가 결과를 채우는지 검증 후 정리.

**Why this priority**: [[principles]] §3/§4 데드 제거·DRY. 020 속성 정체화의 연장.

**Independent Test**: 재분석 후 노드 속성 키 집합에 미사용 속성 0(전수 grep + Neo4j keys 확인).

**Acceptance Scenarios**:

1. **Given** 분석 완료 그래프, **When** 노드 속성 점검, **Then** 저장·소비 없는 속성 0.
2. **Given** table_insights 산출, **When** enrich 단계, **Then** Table/Column `analyzed_description`가 실제로 채워짐(소비 확인) — 안 채워지면 데드로 폐기.

### Edge Cases

- 테이블 분할 대상이 0개(파일에 DML 없음)일 때 → 테이블 분할 콜 0, 나머지 정상.
- 같은 부모 형제가 많을 때 → 배치 처리하되 각 관심사 출력이 폭주하지 않도록 구조적으로 제한.
- 결정론 R/W가 0인 DML 노드(매칭 실패) → 테이블 분할은 결정론 R/W 기준이므로 입력 제외/빈 결과 허용.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: dbms step6 함수분석은 ①요약/식별 ②테이블 인사이트 ③비즈니스 규칙의 **3개 관심사로 분리**되어 생성돼야 한다(한 콜이 4중첩 출력을 동시 생성하지 않는다).
- **FR-002**: 테이블 인사이트 분할의 입력 노드는 **DML 노드(SELECT/INSERT/UPDATE/DELETE/MERGE 등 테이블 접근 노드)로 한정**돼야 한다. 비-DML 노드는 제외.
- **FR-003**: LLM 분석은 정상 입력에서 토큰 상한(length finish)으로 사망하지 않아야 한다 — 출력 구조 단순화로 폭주를 구조적으로 차단(max_tokens 상향 금지).
- **FR-004**: 분석 가치 없는 노드(빈 블록·DECLARE/TRY/TRIGGER_BLOCK 등 구조 노드·trivial)는 LLM 분석에서 제외하되 **그래프 노드와 구조 엣지는 보존**(누락 0).
- **FR-005**: 노드에 저장/소비되지 않는 속성은 폐기한다. table_insights의 소비 경로(Table/Column 설명 분배)가 결과를 채우는지 확인 후, 안 채우면 폐기.
- **FR-006**: 현 그래프 구조(노드 식별자 `id` 통일·관계명·라벨=antlr type 단일·속성 정체화)와 정합해야 한다 — 과거 합본 이전 코드의 단순 복원(revert) 금지.
- **FR-007**: framework 경로의 기존 3분할 동작은 회귀 없이 유지돼야 한다(공유 코드 변경 시 framework 영향 0 확인).
- **FR-008**: 프롬프트 변경 시 KV(prefix) 캐시 친화 — 분할별 `system` 본문은 byte-identical 유지(`check_byte_identical`).

### Key Entities

- **분석 관심사(3분할)**: ①요약(summary·stereotype·logical_name) ②테이블 인사이트(테이블·역할·컬럼 의미) ③비즈니스로직(규칙·예시·질문). 각각 독립 LLM 출력 단위.
- **노드(분석 단위)**: antlr type 라벨 단일, 식별자 `id`. DML 여부가 테이블 분할 적용을 가른다.
- **스킵 대상 노드**: 구조/빈/trivial 노드 — 그래프엔 존재, LLM 분석 비대상.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: RWIS dbms 재분석 1회에서 LLM length-finish 오류 **0건**.
- **SC-002**: 분석된 노드 누락 **0**(모든 비스킵 노드에 summary 존재).
- **SC-003**: 테이블 인사이트 분할 입력 = DML 노드 집합과 100% 일치(비-DML 0건).
- **SC-004**: 스킵 대상 노드의 LLM 콜 0 + 그래프 노드 존재율 100%(구조 엣지 누락 0).
- **SC-005**: 분석 완료 그래프의 노드 속성에 미사용(저장·소비 없음) 속성 **0**.
- **SC-006**: framework 경로 분석 결과 회귀 0(3분할 동작 동일).
- **SC-007**: 분할 프롬프트 `system` byte-identical 유지(diff 0).

## Assumptions

- 재분석 테스트는 RWIS(oracle dbms, 9 .sql)로 수행 — 정상 소스경로(앱 흐름) 기준. (CLI 이중경로 BLOCKER는 별개 선행 항목.)
- 결정론 R/W(테이블 링킹, step4 link_dbms_tables)는 LLM 무관하게 이미 동작 — 테이블 분할의 "어느 테이블" 기준으로 재사용.
- framework의 `three_split_llm_fanout`이 3분할의 기준 구현 — dbms 재설계 시 재사용/정렬 가능 여부는 plan 단계 전수조사로 확정.
- 정확한 step↔노드/관계/속성 매핑·스킵 판별 기준은 plan 단계에서 코드 전수조사로 확정(추측 금지).
